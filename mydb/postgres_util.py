import argparse
import json
import sys
import time
from pathlib import Path

import psycopg
from jinja2 import Template

from . import (
    admin_db,
    aws_util,
    backup_util,
    mydb_config,
    swarm_util,
    touched,
)
from .send_mail import send_mail

dbengine = "Postgres"


def pg_connection_string(user, password, port):
    """Create a PostgreSQL connection string to use with psycopg.connect()"""
    return "".join(
        [
            f"host={mydb_config.container_host}",
            f"port={port}",
            "dbname=postgres",
            f"user={user}",
            f"password={password}",
        ]
    )


def pg_admin_connect(dbname, port):
    """Connect to PostgreSQL as admin user
       return a Postgres connection
    """
    try:
        conn = psycopg.connect(
            host=mydb_config.container_host,
            user=mydb_config.accounts[dbengine]["admin"],
            password=mydb_config.accounts[dbengine]["admin_pass"],
            port=port,
            dbname=dbname,
        )
    except psycopg.Error as e:
        print(f"Error pg_admin_connect: connecting to PostgreSQL: {e}")
        return None
    return conn


def auth_check(dbuser, dbuserpass, port):
    """Connect to Postgres with users credentinals to
    validate that they have access
    """
    connect = pg_connection_string(dbuser, dbuserpass, port)
    try:
        conn = psycopg.connect(connect)
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

    sql_init_script = """-- Create Role
CREATE ROLE {{dbuser}} WITH LOGIN PASSWORD '{{dbuserpass}}';
ALTER USER {{dbuser}} WITH SUPERUSER;

-- Create Database
CREATE DATABASE "{{dbname}}";

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE "{{dbname}}" TO {{dbuser}};

"""

    template = Template(sql_init_script)
    rendered_output = template.render(params)
    params["config_name"] = f"mydb_{params['Name']}_init.sql"
    target_path = "/docker-entrypoint-initdb.d/init.sql"
    config_ref = swarm_util.create_config(params, rendered_output, target_path)
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
    """create Postgres Env"""
    env = [
        f"POSTGRES_USER={mydb_config.accounts[dbengine]['admin']}",
        f"POSTGRES_PASSWORD={mydb_config.accounts[dbengine]['admin_pass']}",
        "POSTGRES_DB=postgres",
    ]
    if auth_meth == "md5":
        env.append("POSTGRES_INITDB_ARGS=--auth-host=md5")
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
    config_data = mydb_config.info[dbengine]
    params["image"] = config_data["images"][0][1]
    params["mapped_db_vol"] = config_data["mapped_volume"]
    params["default_port"] = config_data["default_port"]
    params["service_user"] = config_data["service_user"]
    params["dbname"] = info["Name"]
    params["Name"] = info["Name"]
    params["backup_freq"] = info["BACKUP_FREQ"]
    if "POSTGRES_USER" in info:
        params["dbuser"] = info["POSTGRES_USER"]
    elif "DB_USER" in info:
        params["dbuser"] = info["DB_USER"]
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


def migrate(info):
    """migrate postgres container
    Use meta data from v1 of mydb to create new docker swarm service
    """
    dbname = info["Name"]
    if swarm_util.service_exists(dbname):
        return f"Container name {dbname} already in use"
    dump_prefix = aws_util.lastbackup_s3_prefix(dbname, mydb_config.s3_prefix_migrate)
    if dump_prefix[:5] == "Error":
        return dump_prefix
    volume_name = f"mydb_{dbname}"
    volume_id, error = swarm_util.create_docker_volume(volume_name)
    if error:
        return r"Error creatinge docker volume {volume_name}. Error: {error}"
    params = build_params_postgres(info)
    params["service_name"] = f"mydb_{dbname}"
    params["volume_name"] = volume_name
    # meta_data = json.dumps(params, indent=4)
    # print(f"DEBUG: postgres_util.migrate: {dbname}\nMeta data for container recovery: {meta_data}")
    config_ref = create_init_script(params)
    if config_ref is None:
        return "Error: creating Docker Config"
    service, error = swarm_util.start_service(params, config_ref)
    if service is None:
        return f"{error} {mydb_config.supportOrganization} has been notified"
    params["service_id"] = service.id
    wait_for_postgres(dbname, params["Port"])
    result = pg_restore(params, params, dump_prefix)
    print(f"==== DEBUG: postgres_util.migrate: {dbname}\n{result}")
    return result


def create(params):
    """Create Postgres Container
    Called from mydb_views
    params is created from gerneral_form UI
    """
    data = json.dumps(params, indent=4)
    print(f"DEBUG: postgres_util.create: params before: {data}")
    params["service_name"] = f"mydb_{params['Name']}"
    params["volume_name"] = f"mydb_{params['Name']}"
    if swarm_util.service_exists(params["service_name"]):
        return f"Container name {params['service_name']} already in use"
    volume_id, error = swarm_util.create_docker_volume(params["volume_name"])
    if error:
        return f"Error creatinge docker volume {params['volume_name']}. Error: {error}"
    config_ref = create_init_script(params)
    if config_ref is None:
        return "Error: creating Docker Config"

    config_data = mydb_config.info[dbengine]
    params["mapped_db_vol"] = config_data["mapped_volume"]
    params["default_port"] = config_data["default_port"]
    params["service_user"] = config_data["service_user"]  # 'postgres'
    params["Port"] = admin_db.get_max_port()
    params["env"] = pg_env()
    del params["dbuserpass"]
    params["labels"] = {}
    for label in mydb_config.mydb_v1_meta_data:
        params["labels"][label] = params[label]
    params["labels"]["touched"] = touched.create_date_string()
    service, error = swarm_util.start_service(params, config_ref)
    if service is None:
        return f"{error} Unable to create your DB service {mydb_config.supportOrganization} has been notified"
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
    send_mail(f"MyDB: created {dbengine}", message, mydb_config.supportAdmin)
    return res


def backup(c_id, info, backup_type):
    """Backup all databases for a given Postgres container
    type: str ['User', 'Admin']
    """
    Name = info["Name"]
    backup_id, prefix = aws_util.create_backup_prefix(Name)

    s3_url = f"{mydb_config.AWS_BUCKET_NAME}{prefix}"

    # Dump postgres globals (roles, tablespaces, etc.)
    globals_s3_url = f"{s3_url}{Name}_globals.sql"

    # Build pg_dumpall command for globals
    pg_dumpall_cmd = "pg_dumpall -g "
    pg_dumpall_cmd += f"--host {mydb_config.container_host} "
    pg_dumpall_cmd += f"--port {info['Port']} "
    pg_dumpall_cmd += f"-U {mydb_config.accounts[dbengine]['admin']}"

    # Set PGPASSWORD environment variable for pg_dumpall
    env = {"PGPASSWORD": mydb_config.accounts[dbengine]["admin_pass"]}

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

    # Get list of user databases to be backed up
    try:
        connection = psycopg.connect(
            host=mydb_config.container_host,
            user=mydb_config.accounts[dbengine]["admin"],
            password=mydb_config.accounts[dbengine]["admin_pass"],
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
        pg_dump_cmd += f"--username {mydb_config.accounts[dbengine]['admin']} "
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
    cur.execute("""
        SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolcanlogin
        FROM pg_roles
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


def pg_restore(source, dest, S3_prefix):
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
            result_msg = f" s3 objects: {base_file}..."
    psql_cmd = (f"psql --host {mydb_config.container_host} "
        f"--port {dest['Port']} "
        f"--dbname postgres -U {mydb_config.accounts[dbengine]['admin']}")
    pg_restore = (f"pg_restore --host {mydb_config.container_host} "
        f"--port {dest['Port']} "
        f"-U {mydb_config.accounts[dbengine]['admin']} --dbname XXXX "
        "--clean --if-exists --format=c ")
    password_env = {"PGPASSWORD": mydb_config.accounts[dbengine]["admin_pass"]}

    # Run SQL command file
    SQL_file = None
    for sql_file in backup_files:
        if ".sql" == sql_file[-4:]:
            SQL_file = sql_file
    if not SQL_file:
        return "Could not find a SQL file for PostgreSQL restore. This is bad."

    result_msg += f"Restoring: {dest['dbname']}"
    if dest.get("SQL", "yes") != "no":
        success, msg = backup_util.s3_piped_restore(
            SQL_file, psql_cmd, env=password_env
        )
        if not success:
            return msg
    result_msg += msg

    # Restore data from dump files
    for backup_file in backup_files:
        if ".dump" in backup_file[-5:]:
            instance, dbname, _ = extract_dbname(backup_file)
            print(f"Restoring {instance}{dbname} from {backup_file}")
            restore_command = pg_restore.replace("XXXX", dbname)
            success, msg = backup_util.s3_file_restore(backup_file, restore_command, env=password_env)
            if not success:
                result_msg += f"Error restoring {dbname} from {backup_file}:\n{msg}\n"
            else:
                result_msg += msg

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


def setup_parser():
    parser = argparse.ArgumentParser(
        description="postgres_util CLI testing",
        usage="%(prog)s [options] module_name",
    )
    parser.add_argument(
        "--update-clone",
        required=False,
        action="store_true",
        dest="update",
        help="Used with --clone; The Cloned container will be the latest available DB version",
    )
    parser.add_argument(
        "--clone",
        action="store",
        dest="clone",
        help='Clone container, new container will have "_01" appended to name',
    )
    parser.add_argument(
        "--shm_size",
        action="store",
        dest="shm_size",
        help="Used with --clone option to set the shared mem size (/dev/shm), int or str, (e.g. 1G)",
    )
    parser.add_argument(
        "--restore_admin", action="store_true", help="Restore the Admin DB from S3"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = setup_parser()
    if args.restore_admin:
        restore_admin_db()
