from flask import render_template, session

from . import (
    admin_db,
    mariadb_util,
    migrate_db,
    mongodb_util,
    mydb_config,
    postgres_util,
    swarm_util,
)


def admin_actions(action, args):
    """called from mydb_views after `Select-Selected` UI"""
    container_name = args["container_name"]
    dbengine, info = container_info(container_name, "admin")
    if "Error:" == dbengine[:6]:
        result = dbengine
        header = "Service not found"
    elif action == "audit_db":
        header = f"Audit report for {container_name}"
        if dbengine == "Postgres":
            result = postgres_util.pg_audit(info)
        elif dbengine == "MariaDB":
            result = mariadb_util.mariadb_audit(info)
        else:
            result = f"Audit not implemented for {dbengine}."
    elif action == "restore":
        header = "Restore Output"
        if dbengine == "MariaDB":
            result = mariadb_util.restore(info)
        else:
            result = f"Restore not implemented for {dbengine}."
    elif action == "delete":
        result = swarm_util.admin_delete(container_name, session["username"])
        header = "Remove Service"
    elif action == "connection":
        result = connection_cmd(dbengine, info)
        header = f"{dbengine} Database connection command for {info['dbname']}"
    else:
        result = f"Not sure how you got here {container_name} DBengine: {dbengine}"
        header = "Unknown Operation"
    return render_template(
        "action_result.html",
        title="Database Audit",
        header=header,
        result=result,
    )


def connection_cmd(dbengine, info):
    """create  CLI connection command"""
    admin_user = mydb_config.accounts[dbengine]["admin"]
    admin_pass = mydb_config.accounts[dbengine]["admin_pass"]
    if dbengine == "Postgres":
        cmd = f"PGPASSWORD={admin_pass} psql -h {mydb_config.container_host} "
        cmd += f"-p {info['Port']}  -d {info['dbname']} -U {admin_user}"
        print(f"DEBUG connection_cmd: {cmd}")
        return cmd
    elif dbengine == "MariaDB":
        cmd = f"MYSQL_PWD={admin_pass} mariadb --host {mydb_config.container_host} "
        cmd += f"-P {info['Port']} -u root"
        print(f"DEBUG connection_cmd: {cmd}")
        return cmd
    else:
        return "not implemented"


def migrate_actions(action, args):
    """Called from mydb_views after `Select` actions on Migrate DB"""
    container_name = args["container_name"]
    dbengine, info = container_info(container_name, "migrate")
    if "Error:" == dbengine[:6]:
        return dbengine
    if action == "migrate":
        if dbengine == "Postgres":
            result = postgres_util.migrate(info)
        elif dbengine == "MariaDB":
            result = mariadb_util.migrate(info)
        elif dbengine == "MongoDB":
            result = mongodb_util.migrate(info)
        else:
            result = f"Migration not implemented for {dbengine}"
        return render_template(
            "action_result.html",
            title="Database Migration",
            header=f"Migration results for {container_name}",
            result=result,
        )
    elif action == "migrate_info":
        json_data = migrate_db.display_container_info(container_name)
        print(f"DEBUG {__file__}.migrate_info\n{json_data}")
        return render_template(
            "action_result.html",
            result=json_data,
            title="Container Metadata",
            header=f"Meta data for {container_name}",
        )
    elif action == "migrate_backuplog":
        state_info = migrate_db.get_container_state(container_name)
        if state_info:
            header, body = migrate_db.display_backup_log(state_info.c_id)
            return render_template(
                "action_result.html",
                result=body,
                title="Backup Log",
                header=header,
            )
        else:
            return render_template(
                "action_result.html",
                result=f"Container {container_name} not found",
                title="Error",
                header="Container Not Found",
            )


def user_backup(Name):
    """User requested backup from GUI. Called from <my_views.py>
    page: <select_container> action: backup
    Name: Container name
    """
    (cid, info) = admin_db.get_container_info(Name)
    if cid is None:
        return f"Database container not found: {Name}"
    info["cid"] = cid
    if info["dbengine"] == "Postgres":
        result = postgres_util.backup(info, "User")
    elif info["dbengine"] == "MariaDB":
        result = mariadb_util.backup(info, "User")
    elif info["dbengine"] == "MongoDB":
        result = mongodb_util.backup(info, "User")
    else:
        result = f"Unsupported database engine: {info['dbengine']}"
    return result


def container_info(container_name, admin):
    """Query database for container info

    Standard key: Info["dbengine"]

    Returns: (dbengine_string, Info_dict)
    """
    if admin == "admin":
        state_info = admin_db.get_container_state(container_name)
        if not state_info:
            return f"Error: Container: {container_name} not found in AdminDB", {}
        data = admin_db.get_container_data("", state_info.c_id)
    elif admin == "migrate":
        state_info = migrate_db.get_container_state(container_name)
        if not state_info:
            return f"Error: Container: {container_name} not found in MigrateDB", {}
        data = migrate_db.get_container_data("", state_info.c_id)
    else:
        return "Error: Invalid admin parameter", {}

    # Standard key is dbengine
    info = data["Info"]
    if "username" not in info:
        info["username"] = session["username"]  # Hack to fix V1 meta data
    dbengine = info.get("dbengine", "Unknown")

    return dbengine, info


def restart_con(con_name, dbuser, dbuserpass, username, admin_log=True):
    """restart container called from <select_with_auth()>
    - determin which container type
    - check authentication with db
    - restart docker container
    """
    state_info = admin_db.get_container_state(con_name)
    if not state_info:
        return "Error: Container not found"
    data = admin_db.get_container_data("", state_info.c_id)
    info = data["Info"]
    # Standard key is dbengine
    dbengine = info.get("dbengine", "Unknown")
    port = info["Port"]

    auth = False
    if dbengine == "Postgres":
        auth = postgres_util.auth_check(dbuser, dbuserpass, port)
    elif dbengine == "MariaDB":
        auth = mariadb_util.auth_mariadb(dbuser, dbuserpass, port)
    elif dbengine == "MongoDB":
        auth = mongodb_util.auth_mongodb(dbuser, dbuserpass, port)
    else:
        return "Error: Container type not found."
    if auth:
        result = swarm_util.restart_service(con_name)
        if admin_log and "successfully" in result:
            state_info = admin_db.get_container_state(con_name)
            message = f"Restarted {con_name} user: {username}"
            admin_db.add_container_log(
                state_info.c_id, con_name, message, description=""
            )
    else:
        result = "Error: Authentication failed. You must be the owner to restart"
    return result


def auth_delete(Name, dbuser, dbuserpass, username):
    """stop and remove container"""
    #  get state info (running container)
    state_info = admin_db.get_container_state(Name)
    if not state_info:
        return "Error: Container not found"
    data = admin_db.get_container_data("", state_info.c_id)
    info = data["Info"]
    # Standard key is dbengine
    dbengine = info.get("dbengine", "Unknown")
    port = info["Port"]

    auth = False
    if dbengine == "Postgres":
        auth = postgres_util.auth_check(dbuser, dbuserpass, port)
    elif dbengine == "MariaDB":
        auth = mariadb_util.auth_mariadb(dbuser, dbuserpass, port)
    elif dbengine == "MongoDB":
        auth = mongodb_util.auth_mongodb(dbuser, dbuserpass, port)
    else:
        return "Error: Container type not found"
    if auth:
        print("auth is true; deleting: %s" % Name)
        result = swarm_util.admin_delete(Name, username)
    else:
        result = "Error: Authentication failed. You must be the owner to remove."
    return result
