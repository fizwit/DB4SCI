from calendar import c
import json
import os
import time

import mariadb
from jinja2 import Template

from mydb import migrate_db

from . import (
    admin_db,
    aws_util,
    backup_util,
    mydb_config,
    swarm_util,
    touched,
)
from .send_mail import send_mail

"""
TLS tutorial: https://www.cyberciti.biz/faq/how-to-setup-mariadb-ssl-and-secure-connections-from-clients/
"""

dbengine = "MariaDB"
FiftyGB = 53687091200


def mariadb_admin_connect(port):
    """
    Check if <dbuser> account is authorized user.
    :type port: basestring
    :returns  MariaDB connection object/None
    """
    iport = int(port)
    dbuser = mydb_config.accounts[dbengine]["admin"]
    dbpass = mydb_config.accounts[dbengine]["admin_pass"]
    try:
        conn = mariadb.connect(
            host=mydb_config.container_host, port=iport, user=dbuser, password=dbpass
        )
    except mariadb.Error as e:
        print("ERROR: mariadb_admin_connect: %s" % e)
        return None
    return conn


def auth_mariadb(dbuser, dbpass, port):
    """
    Check if <dbuser> account is authorized user.
    :type dbuser: basestring
    :type dbpass: basestring
    :type port: basestring
    :returns  True/False
    """
    iport = int(port)
    try:
        conn = mariadb.connect(
            host=mydb_config.container_host, port=iport, user=dbuser, password=dbpass
        )
    except mariadb.Error as e:
        print("ERROR: auth_mariadb: %s" % e)
        return False
    conn.close()
    return True


def create_init_script(params):
    """create MariaDB init script to create user account and default database

    MariaDB initialization scripts in /docker-entrypoint-initdb.d/ are executed
    automatically when the container starts for the first time (when data directory is empty).
    """

    sql_init_script = """-- Create Database
CREATE DATABASE IF NOT EXISTS `{{dbname}}`;

-- Create User
CREATE USER IF NOT EXISTS '{{dbuser}}'@'%' IDENTIFIED BY '{{dbuserpass}}';

-- Grant privileges
GRANT ALL PRIVILEGES ON `{{dbname}}`.* TO '{{dbuser}}'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
"""

    template = Template(sql_init_script)
    rendered_output = template.render(params)
    params["config_name"] = f"mydb_{params['Name']}_init.sql"
    target_path = "/docker-entrypoint-initdb.d/init.sql"
    config_ref = swarm_util.create_config(params, rendered_output, target_path)
    return config_ref


def mariadb_audit(Info):
    """Comprehensive audit of a MariaDB instance

    Args:
        Info: Dictionary from database JSONB field containing container metadata

    Lists:
    1. All users/accounts
    2. All databases (excluding system databases)
    3. All tables in each database
    4. Row count for each table

    Returns: formatted audit report string
    """
    report = []
    report.append("=" * 80)
    report.append(f"MariaDB Audit Report")
    report.append(f"Container: {Info.get('Name', 'unknown')}")
    report.append(f"Host: {mydb_config.container_host}")
    report.append(f"Port: {Info['Port']}")
    report.append("=" * 80)
    report.append("")

    conn = mariadb_admin_connect(Info["Port"])
    if conn is None:
        report.append("Failed to connect to MariaDB as admin user.")
        return "\n".join(report)

    cur = conn.cursor()

    # 1. List all users
    report.append("USERS AND ACCOUNTS:")
    report.append("-" * 80)
    cur.execute("""
        SELECT User, Host,
                IF(Super_priv='Y', 'True', 'False') as SuperUser,
                IF(Create_priv='Y', 'True', 'False') as CreatePriv,
                IF(Grant_priv='Y', 'True', 'False') as GrantPriv
        FROM mysql.user
        ORDER BY User, Host
    """)
    users = cur.fetchall()
    report.append(
        f"{'User':<30} {'Host':<20} {'SuperUser':<12} {'Create':<10} {'Grant':<10}"
    )
    report.append("-" * 80)
    for user in users:
        username, host, superuser, create_priv, grant_priv = user
        report.append(
            f"{username:<30} {host:<20} {superuser:<12} {create_priv:<10} {grant_priv:<10}"
        )
    report.append("")

    # 2. List all databases (show all, including system databases)
    report.append("DATABASES:")
    report.append("-" * 80)
    cur.execute("""
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name NOT IN ('information_schema', 'performance_schema')
        ORDER BY schema_name
    """)
    databases = cur.fetchall()

    if not databases:
        report.append("No user databases found.")
        report.append("")
    else:
        for db_row in databases:
            dbname = db_row[0]
            report.append(f"\nDatabase: {dbname}")
            report.append("-" * 80)

            # 3. List all tables in this database
            cur.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema = %s
                AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """,
                (dbname,),
            )
            tables = cur.fetchall()

            if not tables:
                report.append(f"  No tables found in database '{dbname}'")
            else:
                report.append(f"{'Database':<30} {'Table':<40} {'Row Count':<15}")
                report.append("-" * 80)

                # 4. Get row count for each table
                for table_row in tables:
                    schema, tablename = table_row
                    try:
                        # Use COUNT(*) to get row count
                        count_query = f"SELECT COUNT(*) FROM `{schema}`.`{tablename}`"
                        cur.execute(count_query)
                        row_count = cur.fetchone()[0]
                        report.append(f"{schema:<30} {tablename:<40} {row_count:<15,}")
                    except mariadb.Error as e:
                        report.append(
                            f"{schema:<30} {tablename:<40} {'ERROR: ' + str(e):<15}"
                        )

        cur.close()
        conn.close()

        report.append("")
        report.append("=" * 80)
        report.append("Audit Complete")
        report.append("=" * 80)
    return "\n".join(report)


def maria_env() -> list:
    """create MariaDB Env
    Sets up the root (admin) user credentials that MyDB uses for backups and management
    """
    env = [
        f"MARIADB_ROOT_PASSWORD={mydb_config.accounts[dbengine]['admin_pass']}",
        f"MARIADB_USER={mydb_config.accounts[dbengine]['admin']}",
        f"TZ={mydb_config.TZ}",
    ]
    return env


def build_params_mariadb(info) -> dict:
    """Use the container metadata from version 1 of mydb to create a params dict
    This is only required for `migrate`.
    Args:
        info (dict): Container metadata from V1
    Returns:
        dict: Service configuration parameters
    """
    params = {}
    params["dbengine"] = dbengine
    config_data = mydb_config.info[dbengine]
    params["image"] = config_data["images"][0][1]
    params["mapped_db_vol"] = config_data["mapped_volume"]
    params["default_port"] = config_data["default_port"]
    params["service_user"] = config_data["service_user"]
    params["dbname"] = info["Name"]
    params["Name"] = info["Name"]

    if "DB_USER" in info:
        params["dbuser"] = info["DB_USER"]
    elif "MARIADB_USER" in info:
        params["dbuser"] = info["MARIADB_USER"]
    else:
        params["dbuser"] = "admin"  # Default user if not found

    # MariaDB V1 metadata doesn't include user password field
    # Set temporary password - real password will be restored from backup
    #params["dbuserpass"] = "changeme@25"

    params["Port"] = info["Port"]

    # Environment
    params["env"] = maria_env()

    params["labels"] = {
        "Name": params["Name"],
        "DBaaS": "True",
        "backup_freq": info.get("BACKUP_FREQ", ""),
        "contact": info.get("CONTACT", ""),
        "username": params["dbuser"],
        "dbname": params["dbname"],
        "dbuser": params["dbuser"],
        # "dbuserpass": params["dbuserpass"],
        "description": info.get("DESCRIPTION", ""),
        "owner": info.get("OWNER", ""),
        "touched": touched.create_date_string(),
    }
    return params


def migrate(info):
    """migrate MariaDB container from V1 mydb
    Use meta data from v1 of mydb to create new docker swarm service

    Args:
        info (dict): Container metadata from V1
    Returns:
        str: Result message
    """
    dbname = info["Name"]
    service_name = f"mydb_{dbname}"

    if swarm_util.get_service(service_name):
        return f"Service name {service_name} already in use"

    S3_prefix = aws_util.lastbackup_s3_prefix(dbname, mydb_config.s3_prefix_migrate)
    print(f"DEBUG: mariadb_util.migrate S3_prefix: {S3_prefix}")

    volume_name = f"mydb_{dbname}"
    volume_id, error = swarm_util.create_docker_volume(volume_name)
    if error:
        return f"Error creating docker volume {volume_name}. Error: {error}"

    params = build_params_mariadb(info)
    params["service_name"] = service_name
    params["volume_name"] = volume_name

    config_ref = create_init_script(params)
    if config_ref is None:
        return "Error: creating Docker Config"

    service, error = swarm_util.start_service(params, config_ref)
    if service is None:
        return f"{error} {mydb_config.supportOrgName} has been notified"

    wait_for_mariadb(params["Port"])
    params["Start Mesg"] = f"Started! Service_id: {service.id}"
    params["service_id"] = service.id
    meta_data = json.dumps(params, indent=4)
    print(meta_data)

    result = restore_ui(params, S3_prefix)
    print(f"==== DEBUG: mariadb_util.migrate: {dbname}\n{result}")
    return result


def create(params):
    """
    Create MariaDB Docker service.
    Called from mydb_views
    params is created from general_form UI

    :param params: dict
    :return: Help message for end user
    """
    data = json.dumps(params, indent=4)
    print(f"DEBUG: mariadb_util.create: params before: {data}")

    params["service_name"] = f"mydb_{params['Name']}"
    params["volume_name"] = f"mydb_{params['Name']}"

    if swarm_util.get_service(params["service_name"]):
        return f"Service name {params['service_name']} already in use"

    volume_id, error = swarm_util.create_docker_volume(params["volume_name"])
    if error:
        return f"Error creating docker volume {params['volume_name']}. Error: {error}"

    config_ref = create_init_script(params)
    if config_ref is None:
        return "Error: creating Docker Config"

    config_data = mydb_config.info[params["dbengine"]]
    params["mapped_db_vol"] = config_data["mapped_volume"]
    params["default_port"] = config_data["default_port"]
    params["service_user"] = config_data["service_user"]  # 'root'
    params["Port"] = admin_db.get_max_port()
    params["env"] = maria_env()
    del params["dbuserpass"]
    params["labels"] = {}
    for label in mydb_config.mydb_v1_meta_data:
        params["labels"][label] = params[label]
    params["labels"]["touched"] = touched.create_date_string()

    service, error = swarm_util.start_service(params, config_ref)
    if service is None:
        return f"{error} {mydb_config.supportOrgName} has been notified"

    wait_for_mariadb(params["Port"])
    res = "Your MariaDB database server has been created. Use the following command "
    res += "to connect from the Linux command line.\n\n"
    res += f"mariadb --host {mydb_config.container_host} "
    res += f"-P {params['Port']} -D {params['dbname']} "
    res += f"-u {params['dbuser']} -p\n\n"
    res += "You will be prompted to enter your password.\n\n"

    message = (
        f"MyDB created a new {dbengine} database called: {params['service_name']}\n"
    )
    message += f"Created by: {params['owner']} <{params['contact']}>\n"
    send_mail(f"MyDB: created {dbengine}", message, mydb_config.supportEmail)
    return res


def backup(c_id, info, backup_type):
    """Backup all databases for a given MariaDB container
    mariadb-dump is run from the <db4sci> container and piped to S3
    """
    Name = info["Name"]
    backup_id, prefix = aws_util.create_backup_prefix(Name)

    s3_url = f"{mydb_config.AWS_BUCKET_NAME}{prefix}"

    s3_filename = s3_url + f"{Name}_{backup_id}.sql"

    # MariaDB Dump to S3 Backups
    # Note: MariaDB password must be passed via -p flag (no space between -p and password)
    command = "mariadb-dump "
    command += f"--host={mydb_config.container_host} "
    command += f"-P {info['Port']} "
    command += "-u root "
    command += f"-p{mydb_config.accounts[dbengine]['admin_pass']} "
    command += "--single-transaction "
    command += "--all-databases"

    # Create safe command for logging (mask password)
    safe_command = command.replace(
        mydb_config.accounts[dbengine]["admin_pass"], "********"
    )

    # Log backup start
    admin_db.backup_log(
        c_id,
        Name,
        "start",
        backup_id,
        backup_type,
        url=s3_url,
        command=safe_command,
        err_msg="",
    )

    # Use common S3 piped backup function
    # MariaDB doesn't need environment variables - password is in command
    message = f"\nExecuting MariaDB backup to S3: {s3_url}\n"

    success, msg = backup_util.s3_piped_backup(command, s3_filename)
    if not success:
        send_mail("MyDB: MariaDB backup error", message, mydb_config.supportEmail)

    # Log backup end
    admin_db.backup_log(
        c_id,
        Name,
        "end",
        backup_id,
        backup_type,
        url=s3_url,
        command=command,
        err_msg="",
    )

    return message + msg


def wait_for_mariadb(port, timeout=60):
    """Wait for MariaDB to be ready to accept connections

    Args:
        port: Port number where MariaDB is listening
        timeout: Maximum seconds to wait (default 60)

    Returns:
        bool: True if MariaDB is ready, False if timeout
    """
    admin_user = mydb_config.accounts[dbengine]["admin"]
    admin_pass = mydb_config.accounts[dbengine]["admin_pass"]

    print(f"DEBUG: Waiting for MariaDB on port {port} to be ready...")
    start_time = time.time()

    while (time.time() - start_time) < timeout:
        status = auth_mariadb(admin_user, admin_pass, port)
        if status:
            return True
        time.sleep(2)

    print(f"ERROR: MariaDB failed to become ready after {timeout} seconds")
    return False


def restore_ui(dest, S3_prefix):
    """called from UI, with user selected S3 prefix"""
    s3_files = aws_util.get_files_in_s3(S3_prefix)
    if len(s3_files) == 0:
        print(f"ERROR: No files found in S3 prefix {S3_prefix}")
        return False
    print(f"DEBUG: Found {len(s3_files)} files in S3 prefix {S3_prefix}")
    print(f"DEBUG: restore_ui files: {s3_files}")
    for sql_file in s3_files:
        if sql_file.endswith(".sql"):
            print(f"DEBUG: Found SQL file: {sql_file}")
            result = restore(dest, sql_file)
            return result
    else:
        return f"ERROR: No SQL file found in S3 prefix {S3_prefix}"


def restore(dest, S3_file):
    """Restore MariaDB database from S3

    Args:
        dest: Destination container params
        S3_prefix: S3 prefix path for backup files

    Returns:
        str: Result messages from restore operations
    """
    result_msg = ""

    # Build restore command
    # Don't specify a database - the dump file contains CREATE DATABASE statements
    # Note: MariaDB password must be passed via -p flag (no space between -p and password)
    maria_cmd = f"mariadb --host {mydb_config.container_host} "
    maria_cmd += f"-P {dest['Port']} "
    maria_cmd += f"-u {mydb_config.accounts[dbengine]['admin']} "
    maria_cmd += f"-p{mydb_config.accounts[dbengine]['admin_pass']}"

    # Use common S3 piped restore function
    # MariaDB doesn't need environment variables - password is in command
    success, msg = backup_util.s3_piped_restore(S3_file, maria_cmd)

    if not success:
        result_msg += f"Error restoring {S3_file}:\n{msg}\n"
    else:
        result_msg += msg

    result_msg += "Database restore completed from S3."
    print(f"DEBUG: maria_retore: result: {result_msg}")
    return result_msg
