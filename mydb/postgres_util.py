import json
import secrets
import sys
import time
from datetime import date
from pathlib import Path

import psycopg
from psycopg import sql

from . import (
    admin_db,
    aws_util,
    backup_util,
    mydb_config,
    swarm_util,
    touched,
)
from .postgres_hash import postgres_hash
from .send_mail import send_mail

dbengine = "Postgres"


def pg_admin_connect(dbname, port):
    """Connect to PostgreSQL as admin user
       return a Postgres connection
    """
    try:
        conn = psycopg.connect(
            host=mydb_config.container_host,
            user=mydb_config.PG_ADMIN,
            password=mydb_config.PG_ADMIN_PASS,
            port=port,
            dbname=dbname,
        )
    except psycopg.Error as e:
        print(f"Error pg_admin_connect: connecting to PostgreSQL: {e}")
        return None
    return conn


def auth_check(dbuser, dbuserpass, port):
    """Validate a set of credentials against a container.

    Accepts either a database owner account (create_init_script grants them
    SUPERUSER) or the POSTGRES_USER admin account -- both authenticate against
    the `postgres` database the same way.

    Passed as keyword arguments rather than a conninfo string so that a
    password containing a space, quote or backslash cannot corrupt it.
    """
    try:
        conn = psycopg.connect(
            host=mydb_config.container_host,
            port=port,
            dbname="postgres",
            user=dbuser,
            password=dbuserpass,
        )
    except Exception as e:
        print(f"auth_check Error: {e}", file=sys.stderr)
        return False
    conn.close()
    return True


def create_init_script(params):
    """create PostgreSQL init script to create user account and user defined database

    PostgreSQL initialization scripts in /docker-entrypoint-initdb.d/ are executed
    automatically when the container starts for the first time (when data directory is empty).
     <create_type> = ['new', 'migrate', 'retore']
    """

    password_hash = postgres_hash(params['dbuserpass'])
    dbuser = params['dbuser']
    dbname = params['dbname']
    sql_init_script = f"""-- Create Role
CREATE ROLE {dbuser} WITH LOGIN PASSWORD '{password_hash}';
ALTER USER {dbuser} WITH SUPERUSER;

-- Create Database
CREATE DATABASE "{dbname}";

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE "{dbname}" TO {dbuser};

"""

    data = sql_init_script.encode("utf-8")
    config_name = f"mydb_{params['Name']}_init.sql"
    params["config_name"] = config_name
    config_ref = swarm_util.create_config(config_name, data)
    return config_ref

def wait_for_postgres(dbname, port):
    """Wait for PostgreSQL to be ready"""
    start_time = time.time()
    timeout = 60
    time.sleep(4)
    while (time.time() - start_time) < timeout:
        conn = pg_admin_connect(dbname, port)
        if conn is None:
            time.sleep(2)
        else:
            conn.close()
            return True
    print(f"PostgreSQL db: {dbname} is not ready!", file=sys.stderr)
    return False


def pg_env(auth_meth=None) -> list:
    """create Postgres Env
    search TDE for encryption at rest
    """
    env = [
        f"POSTGRES_USER={mydb_config.PG_ADMIN}",
        f"POSTGRES_PASSWORD={mydb_config.PG_ADMIN_PASS}",
        "POSTGRES_DB=postgres",
    ]
    env.append(f"TZ={mydb_config.TZ}")
    return env


def build_params_postgres(info) -> dict:
    """Use the container metadata from version 1 of mydb to create a params dict
    This is only required for `migrate`.
    Args:
        params (dict): Service configuration parameters including image,
            dbname, service_user, env, volume_name, port, default_port,
            and labels.
    """
    params = {}
    params["dbengine"] = info["dbengine"]
    config_data = mydb_config.dbs[dbengine]
    params["image"] = mydb_config.default_image(dbengine)
    params["mapped_db_vol"] = mydb_config.mapped_volume(dbengine, params["image"])
    params["default_port"] = config_data["default_port"]
    params["service_user"] = config_data["service_user"]
    params["dbname"] = info["Name"]
    params["Name"] = info["Name"]
    params["backup_freq"] = info["BACKUP_FREQ"]
    if "POSTGRES_USER" in info:
        params["dbuser"] = info["POSTGRES_USER"]
    elif "DB_USER" in info:
        params["dbuser"] = info["DB_USER"]
    # create_init_script() needs a password to build the role.  v1 metadata
    # carries one only sometimes (see migrate_db.display_active_containers),
    # and whatever is set here lives only until pg_restore loads the globals
    # dump and overwrites pg_authid -- so fall back to a random value rather
    # than a predictable literal, in case the restore never gets that far.
    # reset_admin_password() repairs the admin account after the restore.
    params["dbuserpass"] = (
        info.get("dbuserpass")
        or info.get("POSTGRES_PASSWORD")
        or secrets.token_urlsafe(16)
    )
    params["Port"] = info["Port"]
    # Environtment
    params["env"] = pg_env(auth_meth="md5")
    params["labels"] = {
        "Name": params["Name"],
        "DBaaS": "True",
        "backup_freq": info["BACKUP_FREQ"],
        "contact": info["CONTACT"],
        "username": params["dbuser"],
        "dbname": params["dbname"],
        "dbuser": params["dbuser"],
        "description": info["DESCRIPTION"],
        "owner": info["OWNER"],
        "touched": touched.create_date_string(),
    }
    return params


def reset_admin_password(conn):
    """Put the PG_ADMIN password back after a restore.

    A dump from Postgres 13 or older carries md5 password hashes.  Restoring
    its globals overwrites pg_authid, and a 14+ container authenticates with
    scram-sha-256, which cannot verify an md5 hash -- so every account named in
    the dump, PG_ADMIN included, is locked out the moment the restore lands.

    `conn` must have been opened BEFORE the restore.  PostgreSQL authenticates
    at connect time, so an established session keeps working even after its own
    password hash is overwritten underneath it.  That session is the only way
    back in, which is why the caller holds it across the restore.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(
                    sql.Identifier(mydb_config.PG_ADMIN),
                    sql.Literal(mydb_config.PG_ADMIN_PASS),
                )
            )
        if not conn.autocommit:
            conn.commit()
    except psycopg.Error as e:
        return f"ERROR: could not reset the {mydb_config.PG_ADMIN} password: {e}"
    return f"Reset the {mydb_config.PG_ADMIN} password after the restore."


# Source releases that store passwords as md5 hashes by default.  Postgres 14
# switched the default to scram-sha-256, so anything from 14 on restores into a
# modern container without locking its accounts out.
MD5_ERA_IMAGES = ("postgres:9", "postgres:13")


def source_image(info):
    """Image string from container metadata, case insensitively.

    v1 metadata spells the key "Image"; v2 writes "image".  Returns "" when the
    metadata carries neither.
    """
    for key, value in info.items():
        if key.lower() == "image" and isinstance(value, str):
            return value.strip()
    return ""


def source_has_md5_passwords(info):
    """True when the backup came from a release that stored md5 password hashes.

    Only those need reset_user_passwords(): their role passwords cannot be
    verified by a 14+ container, so every account is locked out after the
    restore.  A backup from 14 or later carries scram verifiers that keep
    working, and rewriting those passwords would be a gratuitous lockout of
    accounts that were fine.
    """
    return source_image(info).lower().startswith(MD5_ERA_IMAGES)


def reset_user_passwords(conn):
    """Give every restored login role a known password.

    Roles come out of the globals dump carrying the source system's md5
    hashes.  A Postgres 14+ container authenticates with scram-sha-256 and
    cannot verify an md5 hash, so every account is locked out even though its
    data restored fine.  This resets them all to ChangeMe@YYYY.MM.DD so owners
    can get back in and set their own.

    Two kinds of role are skipped:
      - PG_ADMIN, which reset_admin_password() has already repaired; resetting
        it here would lock MyDB itself back out.
      - the built in pg_* roles, which are reserved, cannot log in, and reject
        ALTER ROLE.  sql_* is filtered alongside them purely defensively; the
        sql_* names in a database are information_schema tables, not roles.

    `conn` must be an admin session; see reset_admin_password() for why it has
    to have been opened before the restore.
    """
    new_password = f"ChangeMe@{date.today().strftime('%Y.%m.%d')}"
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rolname FROM pg_roles
                WHERE rolcanlogin
                  AND rolname <> %s
                  AND rolname NOT LIKE 'pg\\_%%'
                  AND rolname NOT LIKE 'sql\\_%%'
                ORDER BY rolname
                """,
                (mydb_config.PG_ADMIN,),
            )
            roles = [row[0] for row in cur.fetchall()]
            for rolname in roles:
                cur.execute(
                    sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(
                        sql.Identifier(rolname), sql.Literal(new_password)
                    )
                )
        if not conn.autocommit:
            conn.commit()
    except psycopg.Error as e:
        return f"ERROR: could not reset user passwords: {e}\n"

    if not roles:
        return "No restored login roles needed a password reset.\n"
    report = f"Reset {len(roles)} account(s) to the password: {new_password}\n"
    report += "Owners must change it.  Accounts reset:\n"
    report += "".join(f"    {r}\n" for r in roles)
    return report


def restore_with_open_admin(dest, s3_prefix, label, reset_users=False):
    """Run a restore while holding an admin session open across it.

    The connection is opened first and handed to pg_restore(), which uses it
    to repair the admin password between the globals restore and the per
    database dumps -- see reset_admin_password() for why the ordering matters.
    If it cannot be opened we refuse to restore at all, because there would be
    no way to recover the admin account afterwards.
    """
    conn = pg_admin_connect("postgres", dest["Port"])
    if conn is None:
        return (f"Error: cannot connect to {label} as {mydb_config.PG_ADMIN} on port "
                f"{dest['Port']}. Refusing to restore -- without this session the "
                "admin account could not be recovered after the restore.")
    # CREATE DATABASE cannot run inside a transaction block
    conn.autocommit = True
    try:
        result = pg_restore(dest, dest, s3_prefix, admin_conn=conn,
                            reset_users=reset_users)
    finally:
        conn.close()
    return result


def migrate(info):
    """migrate postgres container
    Use meta data from v1 of mydb to create new docker swarm service
    """
    dbname = info["Name"]
    if swarm_util.get_service(dbname):
        return f"Container name {dbname} already in use"
    dump_prefix = aws_util.lastbackup_s3_prefix(dbname, mydb_config.s3_prefix_migrate)
    if dump_prefix[:5] == "Error":
        return dump_prefix
    volume_name = f"mydb_{dbname}"
    swarm_util.create_docker_volume(volume_name)
    params = build_params_postgres(info)
    params["service_name"] = f"mydb_{dbname}"
    params["volume_name"] = volume_name
    config_ref = create_init_script(params)
    # the hash is baked into the init script now; keep the plaintext out of
    # params, which start_service() dumps to the log as JSON
    del params["dbuserpass"]

    service, error = swarm_util.start_service(params, config_ref)
    if service is None:
        return f"{error} {mydb_config.supportOrgName} has been notified"
    params["service_id"] = service.id
    wait_for_postgres(dbname, params["Port"])
    result = restore_with_open_admin(
        params, dump_prefix, dbname, reset_users=source_has_md5_passwords(info)
    )
    print(f"==== DEBUG: postgres_util.migrate: {dbname}\n{result}")
    return result


def restore(info, s3_prefix):
    """Restore an existing container from an S3 backup prefix.
    Called from mydb_actions.restore()."""
    dest = {"dbname": info.get("dbname", info["Name"]), "Port": info["Port"]}
    return restore_with_open_admin(
        dest, s3_prefix, info["Name"], reset_users=source_has_md5_passwords(info)
    )


def create(params):
    """Create Postgres Container
    Called from mydb_views
    params is created from gerneral_form UI
    """
    if mydb_config.FLASK_DEBUG:
        data = json.dumps(params, indent=4)
        print(f"DEBUG: postgres_util.create: params before: {data}")
    params["service_name"] = f"mydb_{params['Name']}"
    params["volume_name"] = f"mydb_{params['Name']}"
    if swarm_util.get_service(params["service_name"]):
        return f"Container name {params['service_name']} already in use"
    swarm_util.create_docker_volume(params["volume_name"])
    config_ref = create_init_script(params)

    config_data = mydb_config.dbs[dbengine]
    params["mapped_db_vol"] = mydb_config.mapped_volume(dbengine, params["image"])
    params["default_port"] = config_data["default_port"]
    params["service_user"] = config_data["service_user"]  # 'postgres'
    params["Port"] = admin_db.get_avail_port()
    params["env"] = pg_env()
    del params["dbuserpass"]
    params["labels"] = {}
    for label in mydb_config.mydb_v1_meta_data:
        params["labels"][label] = params[label]
    params["labels"]["touched"] = touched.create_date_string()
    service, error = swarm_util.start_service(params, config_ref)
    if service is None:
        return f"{error} Unable to create your DB service {mydb_config.supportOrgName} has been notified"
    res = "Your database server has been created. Use the following command "
    res += "to connect from the Linux command line.\n\n"
    res += f"psql -h {mydb_config.container_host} "
    res += f"-p {params['Port']} -d {params['dbname']} "
    res += f"-U {params['dbuser']} --password\n\n"
    res += "If you would like to connect to the database without entering a "
    res += "password, create a .pgpass file in your home directory.\n"
    res += (
        "Set permissions to 600. Format is hostname:port:database:username:password.\n"
    )
    res += "Cut/paste this line and place in your /home/user/.pgpass file.\n\n"
    res += f"{mydb_config.container_host}:{params['Port']}:{params['dbname']}"
    res += f":{params['dbuser']}:PASSWORD\n\n"
    res += "To use psql on the linux command line load the PostgreSQL module.\n"
    res += "module load PostgreSQL\n\n"

    message = (
        f"Mydb created a new {dbengine} database called: {params['service_name']}\n"
    )
    message += f"Created by: {params['owner']} <{params['contact']}>\n"
    send_mail(f"MyDB: created {dbengine}", message, mydb_config.supportEmail)
    return res


def pg_backup(info, backup_type="User", c_id=None):
    """Backup all databases for a given Postgres container.

    Runs pg_dumpall/pg_dump here in DB4SCI over the network (--host/--port) and
    pipes each dump straight to S3.  This works across Postgres majors because
    the client tools can dump any server at or below their own major version --
    so DB4SCI just has to ship a client at least as new as the newest server it
    deploys (see the postgresql-client version in the Dockerfile).  It also
    works regardless of which swarm node the container runs on, since it only
    needs the published TCP port, not local access to the container.

    Args:
        info (dict): container metadata (needs Name and Port)
        backup_type (str): one of ['User', 'Admin'] -- recorded in the backup log
        c_id: admin_db container id for logging; looked up from Name when omitted
    """
    Name = info["Name"]
    if c_id is None:
        state = admin_db.get_container_state(Name)
        if state is None:
            return f"Error: container {Name} not found in Admin DB; cannot back up."
        c_id = state.c_id

    backup_id, prefix = aws_util.create_backup_prefix(Name)
    s3_url = f"{mydb_config.AWS_BUCKET_NAME}{prefix}"

    # Dump postgres globals (roles, tablespaces, etc.)
    globals_s3_url = f"{s3_url}{Name}_globals.sql"

    # Build pg_dumpall command for globals
    pg_dumpall_cmd = "pg_dumpall -g "
    pg_dumpall_cmd += f"--host {mydb_config.container_host} "
    pg_dumpall_cmd += f"--port {info['Port']} "
    pg_dumpall_cmd += f"-U {mydb_config.PG_ADMIN}"

    # Set PGPASSWORD environment variable for pg_dumpall
    env = {"PGPASSWORD": mydb_config.PG_ADMIN_PASS}

    # Log backup start
    admin_db.backup_log(
        c_id,
        Name,
        "start",
        backup_id,
        backup_type,
        url=s3_url,
        command=pg_dumpall_cmd,
        err_msg="",
    )

    message = f"\nExecuting Postgres backup to S3: {s3_url}\n"
    message += f"Backing up globals to: {globals_s3_url}\n"

    # Use common S3 piped backup function with environment variables
    success, msg = backup_util.s3_piped_backup(
        pg_dumpall_cmd,
        globals_s3_url,
        env=env,
    )

    if not success:
        message += f"Error backing up globals:\n{msg}"
        return message
    message += msg

    # Get list of user databases to be backed up.  psycopg works across server
    # versions, so this query still runs from DB4SCI over the network.
    try:
        connection = psycopg.connect(
            host=mydb_config.container_host,
            user=mydb_config.PG_ADMIN,
            password=mydb_config.PG_ADMIN_PASS,
            port=info["Port"],
            dbname="postgres",
        )
    except Exception as e:
        message = "Error: MyDB Postgres Backup; "
        message += f"psycopg connect: container: {Name}, Port: {info['Port']}"
        message += f"message: {e}, "
        print(f"ERROR: {message}")
        return message

    cur = connection.cursor()
    select = "SELECT datname FROM pg_database WHERE datname "
    select += "<> 'postgres' AND datistemplate=false"
    cur.execute(select)
    dbs = cur.fetchall()
    connection.close()

    message += f"\nBacking up {len(dbs)} database(s):\n"
    # Back up each database
    pg_dump_cmd = ""
    for db in dbs:
        dbname = db[0]
        s3_dump_url = f"{s3_url}{Name}_{dbname}.dump"

        # Build pg_dump command
        pg_dump_cmd = "pg_dump "
        pg_dump_cmd += f"--dbname {dbname} "
        pg_dump_cmd += "--lock-wait-timeout=5000 "
        pg_dump_cmd += f"--host {mydb_config.container_host} "
        pg_dump_cmd += f"--port {info['Port']} "
        pg_dump_cmd += f"--username {mydb_config.PG_ADMIN} "
        pg_dump_cmd += "-F c"

        # Use common S3 piped backup function with environment variables
        success, msg = backup_util.s3_piped_backup(pg_dump_cmd, s3_dump_url, env=env)

        if not success:
            message += f"\nDatabase: {dbname}\n"
            message += f"Error: {msg}\n"
        else:
            message += f"\nDatabase: {dbname} written to: {s3_dump_url}\n"
            message += msg

    admin_db.backup_log(
        c_id,
        Name,
        "end",
        backup_id,
        backup_type,
        url=s3_url,
        command=pg_dump_cmd,
        err_msg=message,
    )

    return message


def pg_audit(Info):
    """Comprehensive audit of a PostgreSQL instance

    Args:
        Info: Dictionary from database JSONB field containing container metadata
              Expected keys: Port, POSTGRES_USER, POSTGRES_PASSWORD

    Lists:
    1. All users/roles
    2. All databases (excluding template0, template1, postgres)
    3. All tables in each database
    4. Row count for each table

    Returns: formatted audit report string
    """
    report = []
    report.append("=" * 80)
    report.append("PostgreSQL Audit Report")
    report.append(f"Container: {Info.get('Name', 'unknown')}")
    report.append(f"Host: {mydb_config.container_host}")
    report.append(f"Port: {Info['Port']}")
    report.append("=" * 80)
    report.append("")

    # Connect to postgres database to get system info
    conn = pg_admin_connect("postgres", Info["Port"])
    if conn is None:
        return "\nUnable to connect to PostgreSQL database.\n".join(report)
    cur = conn.cursor()

    # 1. List all users/roles
    report.append("USERS AND ROLES:")
    report.append("-" * 80)
    # exclude pg_* reserved names by PostgreSQL
    cur.execute("""
        SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolcanlogin
        FROM pg_roles
        WHERE rolname NOT LIKE 'pg\\_%'
        ORDER BY rolname
    """)
    users = cur.fetchall()
    report.append(
        f"{'Role Name':<30} {'Superuser':<12} {'CreateDB':<10} {'CreateRole':<12} {'CanLogin':<10}"
    )
    report.append("-" * 80)
    for user in users:
        rolname, rolsuper, rolcreatedb, rolcreaterole, rolcanlogin = user
        report.append(
            f"{rolname:<30} {str(rolsuper):<12} {str(rolcreatedb):<10} {str(rolcreaterole):<12} {str(rolcanlogin):<10}"
        )
    report.append("")

    # 2. List all databases (exclude system databases)
    report.append("DATABASES:")
    report.append("-" * 80)
    cur.execute("""
        SELECT datname
        FROM pg_database
        WHERE datname NOT IN ('template0', 'template1', 'postgres')
        AND datistemplate = false
        ORDER BY datname
    """)
    databases = cur.fetchall()
    if not databases:
        report.append("No user databases found.")
        report.append("")
    databases.insert(0, ("postgres",))
    for db_row in databases:
        dbname = db_row[0]
        report.append(f"\nDatabase: {dbname}")
        report.append("-" * 80)
        if dbname != "postgres":
            conn = pg_admin_connect(dbname, Info["Port"])
            cur = conn.cursor()

        # 3. List all tables in this database
        cur.execute("""
            SELECT schemaname, tablename
            FROM pg_tables
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY schemaname, tablename
        """)
        tables = cur.fetchall()

        if not tables:
            report.append(f"  No user tables found in database '{dbname}'")
        else:
            report.append(f"{'Schema':<30} {'Table':<40} {'Row Count':<15}")
            report.append("-" * 80)

            # 4. Get row count for each table
            for table_row in tables:
                schemaname, tablename = table_row
                try:
                    # Use count(*) to get row count
                    count_query = f'SELECT COUNT(*) FROM "{schemaname}"."{tablename}"'
                    cur.execute(count_query)
                    row_count = cur.fetchone()[0]
                    report.append(f"{schemaname:<30} {tablename:<40} {row_count:<15,}")
                except Exception as e:
                    report.append(
                        f"{schemaname:<30} {tablename:<40} {'ERROR: ' + str(e):<15}"
                    )

        cur.close()
        conn.close()

    report.append("")
    report.append("=" * 80)
    report.append("Audit Complete")
    report.append("=" * 80)
    if mydb_config.FLASK_DEBUG == "1":
        print(report)
    return "\n".join(report)


def extract_dbname(s3_object):
    """Extract instance, dbname, and dumpfile from S3 object path
    Format of s3_object: <instance>/<dbname>_<timestamp>.dump
    This is used for PostgreSQL database backups, where the instance name
    has more than one database.

    S3 Object path format: <'s3:/bucket_name/prefix/instanceName/instanceName_YYYY.MM.DD_HH.MM.SS/instance_dbname.dump>
    """
    path = Path(s3_object.rstrip('\n'))
    dumpfile = path.name
    instance = path.parent.parent.name
    dbname = dumpfile.replace(instance + '_', '', 1)[:-5]
    return instance, dbname, dumpfile


def ensure_database(conn, dbname):
    """CREATE DATABASE <dbname> unless it already exists.

    pg_restore --dbname needs the database to exist first, and the globals
    dump cannot supply it: backup() uses `pg_dumpall -g`, which emits roles and
    tablespaces only.  create_init_script() makes just the one database named
    after the container, so an instance holding several databases needs the
    rest created here.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        if cur.fetchone():
            return f"database {dbname} already exists\n"
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    return f"created database {dbname}\n"


def pg_restore(source, dest, S3_prefix, admin_conn=None, reset_users=False):
    """Restore Postgres database from S3
    <source> and <dest> are container data structure: like `params`
    Postgres backup has a minimum of 3 files; control file, SQL file, dump file
    There may be multiple dump files for additional DB's
    require connection string to restore target

    Returns: all the stdout from the commands. If an error occures, add the stderr, to
       the messages.  pg_dump restore never works without some kind of
       error messages/warnings.
    """

    result_msg = ""
    backup_files = aws_util.get_files_in_s3(S3_prefix)
    if mydb_config.FLASK_DEBUG == "1":
        print(f"DEBUG: pg_restore: {dest['dbname']} backup_files: {backup_files}")
    if len(backup_files) == 0:
        return "Could not find any files to restore PostgreSQL database."
    else:
        result_msg = f"pg_restore: restoring from {S3_prefix}\n"
        for backup_file in backup_files:
            path = Path(backup_file)
            base_file = path.name
            result_msg += f" s3 objects: {base_file}...\n"
    psql_cmd = (f"psql --host {mydb_config.container_host} "
        f"--port {dest['Port']} "
        f"--dbname postgres -U {mydb_config.PG_ADMIN}")
    pg_restore = (f"pg_restore --host {mydb_config.container_host} "
        f"--port {dest['Port']} "
        f"-U {mydb_config.PG_ADMIN} --dbname XXXX "
        "--clean --if-exists --format=c ")
    password_env = {"PGPASSWORD": mydb_config.PG_ADMIN_PASS}

    # Run SQL command file
    SQL_file = None
    for sql_file in backup_files:
        if ".sql" == sql_file[-4:]:
            SQL_file = sql_file
    if not SQL_file:
        return "Could not find a SQL file for PostgreSQL restore. This is bad."

    result_msg += f"Restoring: {dest['dbname']}\n"
    if dest.get("SQL", "yes") != "no":
        success, msg = backup_util.s3_piped_restore(
            SQL_file, psql_cmd, env=password_env
        )
        if not success:
            return msg
        result_msg += msg

    # The globals dump has just overwritten pg_authid with the source system's
    # md5 password hashes.  Every pg_restore below opens its OWN connection
    # using PGPASSWORD, and a 14+ container authenticates with scram-sha-256,
    # which cannot verify an md5 hash -- so unless the admin password is
    # repaired right here, all of them fail to authenticate and the databases
    # come back empty.  admin_conn was opened before the restore and is still
    # authenticated; see reset_admin_password().
    if admin_conn is not None:
        result_msg += reset_admin_password(admin_conn) + "\n"

    # Restore data from dump files
    for backup_file in backup_files:
        if ".dump" in backup_file[-5:]:
            instance, dbname, _ = extract_dbname(backup_file)
            print(f"Restoring {instance}{dbname} from {backup_file}")
            if admin_conn is not None:
                result_msg += ensure_database(admin_conn, dbname)
            restore_command = pg_restore.replace("XXXX", dbname)
            success, msg = backup_util.s3_file_restore(backup_file, restore_command, env=password_env)
            if not success:
                result_msg += f"Error restoring {dbname} from {backup_file}:\n{msg}\n"
            else:
                result_msg += msg

    # Roles restored from the globals dump still carry md5 hashes and cannot
    # authenticate against a 14+ container, so their data is unreachable until
    # the passwords are replaced.  Done last so it also covers the databases
    # that were just restored.
    if admin_conn is not None and reset_users:
        result_msg += "\n" + reset_user_passwords(admin_conn)
    elif admin_conn is not None:
        result_msg += ("\nSource is Postgres 14 or later, so its scram password "
                       "verifiers restored intact; accounts left untouched.\n")

    result_msg += "Database restore completed from S3."
    return result_msg


def restore_admin_db():
    """grab the backup of mydb_admin from S3 in '/prod' prefix.
    Once Version 2 goes live the version 1 DBs will need to be archived.
    So maybe change the prefix to /archive once V2 is live and copy the
    prod to /archive - Nov 2025
    """
    dump_prefix = aws_util.lastbackup_s3_prefix(
        "mydb_admin", mydb_config.s3_prefix_migrate
    )
    if dump_prefix[:5] == "Error":
        return f"Error: could not retrieve last backup prefix: {dump_prefix}"
    files = aws_util.get_files_in_s3(dump_prefix)
    if len(files) == 0:
        return f"Error: no files found in S3 for prefix {dump_prefix}"
    # Build pg_restore command
    password_env = {"PGPASSWORD": mydb_config.accounts["admindb"]["v1_admin_pass"]}
    pg_restore = " ".join(
        [
            "pg_restore",
            f"--host {mydb_config.container_host}",
            "--port 32008",
            "--dbname=mydb_admin",
            f"--username={mydb_config.accounts['admindb']['admin']}",
        ]
    )

    # Use common S3 piped restore function
    message = "Restoring admin_db\n"
    for file in files:
        if ".dump" in file:
            success, result_msg = backup_util.s3_piped_restore(
                file, pg_restore, env=password_env
            )
            if not success:
                return result_msg
            message += result_msg
    return message
