import argparse
import json
import sys

from jinja2 import Template
from pymongo import MongoClient
from pymongo.errors import (
    ConnectionFailure,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from . import (
    admin_db,
    aws_util,
    backup_util,
    mydb_config,
    swarm_util,
    touched,
)

dbengine = "MongoDB"


def auth_mongodb(dbuser, dbpass, port):
    connection_string = (
        f"mongodb://{dbuser}:{dbpass}@{mydb_config.container_host}:{port}/admin"
    )
    client = MongoClient(connection_string, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except (ConnectionFailure, OperationFailure, ServerSelectionTimeoutError) as e:
        print(f"Error connecting to MongoDB. port={port} {e}", file=sys.stderr)
        return False
    client.close()
    return True


def create_init_script(params):
    """Create admin roles and user account

    MongoDB initialization scripts in /docker-entrypoint-initdb.d/ are executed
    automatically when the container starts for the first time (when data directory is empty).
    """
    init_user_js = """// Create the user's database and user account
use {{dbname}};
db.collectionName.insertOne({ message: "Hello from MyDB", dbname: "{{dbname}}" });

// Create user with dbOwner role - full admin rights to this database
use admin;
db.createUser({
    user: '{{dbuser}}',
    pwd: '{{dbuserpass}}',
    roles: [
        {role: 'dbOwner', db: '{{dbname}}'},
        {role: "dbAdmin", db: "admin" },
        {role: "userAdminAnyDatabase", db: "admin" },
        {role: "readWriteAnyDatabase", db: "admin" }
       ]
});

"""
    template = Template(init_user_js)
    rendered_output = template.render(params)
    params["config_name"] = f"mydb_{params['Name']}_init_user.js"
    target_path = "/docker-entrypoint-initdb.d/init_user.js"
    return swarm_util.create_config(params, rendered_output, target_path)


def mongo_env(dbname):
    """Create MongoDB container environment

    Args:
        dbname: Database name for MONGO_INITDB_DATABASE
    """
    dbengine = "MongoDB"
    env = [
        f"MONGO_INITDB_ROOT_USERNAME={mydb_config.accounts[dbengine]['admin']}",
        f"MONGO_INITDB_ROOT_PASSWORD={mydb_config.accounts[dbengine]['admin_pass']}",
        f"MONGO_INITDB_DATABASE={dbname}",
        f"TZ={mydb_config.TZ}",
    ]
    return env


def build_params_mongo(info):
    """Build params dict from container metadata for migration

    This is only required for `migrate`.
    Args:
        info (dict): Container metadata from version 1 of mydb
    Returns:
        dict: Service configuration parameters
    """
    params = {}
    params["dbengine"] = info["dbengine"]
    params["image"] = mydb_config.info[dbengine]["images"][0][1]
    params["default_port"] = mydb_config.info[dbengine]["default_port"]
    params["service_user"] = mydb_config.info[dbengine]["service_user"]
    params["dbname"] = info["Name"]  # dbname is missing in V1 metadata
    params["Name"] = info["Name"]
    if "DB_USER" in info:
        params["dbuser"] = info["DB_USER"]
    else:
        params["dbuser"] = info.get("dbuser", "admin")
    params["Port"] = info["Port"]
    params["env"] = mongo_env(info["Name"])
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
    """Migrate MongoDB container from v1 to v2

    Use metadata from v1 of mydb to create new docker swarm service
    and restore data from S3 backup

    Args:
        info (dict): Container metadata from migrate_db
    Returns:
        str: Result message
    """
    import time

    dbname = info["Name"]
    if swarm_util.get_service(dbname):
        return f"Container name {dbname} already in use"

    # Create Docker volume
    volume_name = f"mydb_{dbname}"
    volume_id, error = swarm_util.create_docker_volume(volume_name)
    if error:
        return f"Error creating docker volume {volume_name}. Error: {error}"

    # Build params from v1 metadata
    params = build_params_mongo(info)
    params["service_name"] = f"mydb_{dbname}"
    params["volume_name"] = volume_name
    params["mapped_db_vol"] = mydb_config.info[dbengine]["mapped_volume"]

    # Create init script config
    config_ref = create_init_script(params)
    if config_ref is None:
        return "Error: creating Docker Config"

    # Start the service
    service, error = swarm_util.start_service(params, config_ref)
    if service is None:
        return f"{error} {mydb_config.supportOrgName} has been notified"

    params["Start Mesg"] = f"Started! Service_id: {service.id}"
    params["service_id"] = service.id
    meta_data = json.dumps(params, indent=4)
    print(meta_data)

    # Wait for service to be ready
    time.sleep(4)

    # Restore from S3
    S3_path = aws_util.lastbackup_s3_prefix(dbname, mydb_config.s3_prefix_migrate)
    print(f"DEBUG migrate mongodb Backup location: {S3_path}")
    result = mongo_restore(params, S3_path)
    print(f"==== DEBUG: mongodb_util.migrate: {dbname}\n{result}")
    return result


def mongo_restore(dest, s3_prefix):
    """Restore MongoDB database from S3 backup archive

    MongoDB backup uses mongodump --archive which creates a single file.
    Restore using mongorestore --archive.

    return str: Result message with restore status
    """
    archive_url = f"{mydb_config.AWS_BUCKET_NAME}/{s3_prefix}/archive"
    print(f"MongoDB restore - S3 URL: {archive_url}")

    # Build mongorestore command
    restore_cmd = f"mongorestore --username {mydb_config.accounts[dbengine]['admin']} "
    restore_cmd += f"--password {mydb_config.accounts[dbengine]['admin_pass']} "
    restore_cmd += f"--host {mydb_config.container_host} "
    restore_cmd += f"--port {dest['Port']} "
    restore_cmd += "--authenticationDatabase admin "
    restore_cmd += "--archive"

    # Use common S3 piped restore function
    success, result_msg = backup_util.s3_piped_restore(
        archive_url,
        restore_cmd,
        timeout=1800,  # 30 minute timeout for large restores
        mask_password=mydb_config.accounts[dbengine]["admin_pass"],
    )

    if not success:
        return result_msg

    result_msg += "\nMongoDB database restore completed from S3."
    return result_msg


def create_mongodb(params):
    """Create MongoDB Service in Docker Swarm
    Called from mydb_views
    params is created from general_form UI
    """
    from .send_mail import send_mail

    dbengine = params["dbengine"]
    data = json.dumps(params, indent=4)
    print(f"DEBUG: mongodb_util.create_mongodb: params before: {data}")

    params["service_name"] = f"mydb_{params['Name']}"
    params["volume_name"] = f"mydb_{params['Name']}"

    # Check if service already exists
    if swarm_util.get_service(params["service_name"]):
        return f"Service name {params['service_name']} already in use"

    # Create Docker volume
    volume_id, error = swarm_util.create_docker_volume(params["volume_name"])
    if error:
        return f"Error creating docker volume {params['volume_name']}. Error: {error}"

    # Create init script config
    config_ref = create_init_script(params)
    if config_ref is None:
        return "Error: creating Docker Config"

    # Get config data
    config_data = mydb_config.info[dbengine]
    params["mapped_db_vol"] = config_data["mapped_volume"]
    params["default_port"] = config_data["default_port"]
    params["service_user"] = config_data["service_user"]
    params["Port"] = admin_db.get_max_port()
    params["env"] = mongo_env(params["dbname"])

    # Create labels
    params["labels"] = {}
    for label in mydb_config.mydb_v1_meta_data:
        params["labels"][label] = params[label]
    params["labels"]["touched"] = touched.create_date_string()

    # Start the service
    service, error = swarm_util.start_service(params, config_ref)
    if service is None:
        return f"{error} {mydb_config.supportOrgName} has been notified"

    # Build response message
    res = "Your MongoDB database server has been created.\n\n"
    res += f'MongoDB URI: "mongodb://{params["dbuser"]}:{params["dbuserpass"]}@'
    res += f'{mydb_config.container_host}:{params["Port"]}/{params["dbname"]}"\n\n'
    res += "Use the mongo shell to connect:\n"
    connection = f"mongosh -u {params['dbuser']} --host {mydb_config.container_host} "
    connection += f"--port {params['Port']} "
    connection += f"--authenticationDatabase {params['dbname']} -p\n\n"
    print(f"DEBUG: mongodb_util: {connection}  password({params['dbuserpass']})")
    # Send notification email
    message = (
        f"MyDB created a new {dbengine} database called: {params['service_name']}\n"
    )
    message += f"Created by: {params['owner']} <{params['contact']}>\n"
    send_mail(f"MyDB: created {dbengine}", message, mydb_config.supportEmail)

    return res + connection


def backup(info, type):
    """Backup MongoDB database
    use mongodump with --archive which creates a single file
    """
    Name = info["Name"]
    backup_id, prefix = aws_util.create_backup_prefix(Name)
    s3_url = f"{mydb_config.AWS_BUCKET_NAME}{prefix}archive"

    # Build mongodump command
    mongodump_cmd = [
        "mongodump",
        "--username",
        mydb_config.accounts[dbengine]["admin"],
        "--password",
        mydb_config.accounts[dbengine]["admin_pass"],
        "--host",
        mydb_config.container_host,
        "--port",
        str(info["Port"]),
        "--archive",
    ]

    # Log backup start
    command_str = " ".join(mongodump_cmd)
    safe_command = command_str.replace(
        mydb_config.accounts[dbengine]["admin_pass"], "********"
    )
    admin_db.backup_log(
        info["cid"],
        Name,
        "start",
        backup_id,
        type,
        url=s3_url,
        command=safe_command,
        err_msg="",
    )

    message = f"\nExecuting Mongo backup to S3: {mydb_config.AWS_BUCKET_NAME}\n"
    message += f"Executing: {safe_command}\n"
    message += f"     to: {s3_url}\n"

    # Use common S3 piped backup function
    success, msg = backup_util.s3_piped_backup(
        mongodump_cmd,
        s3_url,
        mask_password=mydb_config.accounts[dbengine]["admin_pass"],
    )

    if not success:
        message += f"\nDatabase: {Name}\n"
        message += f"Error: {msg}\n"
    else:
        message += f"\nDatabase: {Name} written to: {s3_url}\n"
        message += msg

    # Log backup end
    admin_db.backup_log(
        info["cid"], Name, "end", backup_id, type, s3_url, safe_command, message
    )
    return message


def setup_parser():
    parser = argparse.ArgumentParser(
        description="Mongo CLI testing",
        usage="%(prog)s [options] module_name",
    )
    parser.add_argument(
        "--test-init", action="store_true", required=False, help="test SQL init script"
    )
    return parser.parse_args()


if __name__ == "__main__":
    from . import test_params

    params = test_params.get_test_params()
    args = setup_parser()
