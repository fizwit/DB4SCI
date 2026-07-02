import json
import tomllib
from functools import wraps
from pathlib import Path

from flask import (
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

from . import app

# from . import mongodb_util
from . import (
    AD_auth,
    admin_db,
    aws_util,
    backup_util,
    mariadb_util,
    migrate_db,
    mongodb_util,
    mydb_actions,
    mydb_config,
    postgres_util,
    swarm_util,
)

# Application metadata is sourced from pyproject.toml (single source of truth)
try:
    _meta = tomllib.loads(
        (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    )
    _project = _meta.get("project", {})
    __version__ = _project.get("version", "unknown")
    __author__ = (_project.get("authors") or [{}])[0].get("email", "")
    __release_date__ = _meta.get("tool", {}).get("db4sci", {}).get("release_date", "")
except (OSError, tomllib.TOMLDecodeError):
    __version__ = "unknown"
    __release_date__ = ""
    __author__ = ""


def get_template_context():
    """Return common context variables for all templates"""
    return {
        "logo_path": mydb_config.organizationLogo,
        "org_name": mydb_config.institutionName,
        "version": __version__,
        "release_date": __release_date__,
        "institutionName": mydb_config.institutionName,
        "supportOrgName": mydb_config.supportOrgName,
        "supportOrgEmail": mydb_config.supportOrgEmail,
    }


def auth_required(func):
    @wraps(func)
    def decorated_function(*args, **kwargs):
        if session.get("logged_in"):
            return func(*args, **kwargs)
        else:
            return redirect(url_for("login"))

    return decorated_function


def admin_required(func):
    @wraps(func)
    def decorated_function(*args, **kwargs):
        if session.get("admin_user"):
            return func(*args, **kwargs)
        else:
            return render_template("index.html", **get_template_context())

    return decorated_function


@app.route("/")
@app.route("/index")
def index():
    if session.get("logged_in", False):
        return render_template("index.html", **get_template_context())
    else:
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        auth, info = AD_auth.is_valid(username, password)

        if auth == "Good":
            # Store user data in session
            session.permanent = True
            session["logged_in"] = True
            for user_key in info.keys():
                session[user_key] = info[user_key]
            if username in mydb_config.admins:
                session["admin_user"] = True
            else:
                session["admin_user"] = False

            return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/list_containers/")
@auth_required
def list_containers():
    (header, body) = admin_db.display_active_containers()
    return render_template(
        "dblist.html", title="Active Containers", dbheader=header, dbs=body
    )


@app.route("/migrate_email")
@auth_required
@admin_required
def migrate_email():
    (header, body) = migrate_db.display_email_list()
    return render_template(
        "action_result.html", result=body, title=f"MigrateDB Data", header=header
    )


@app.route("/migrate_restore")
@auth_required
@admin_required
def migrate_restore():
    body = postgres_util.restore_admin_db()
    return render_template(
        "action_result.html",
        result=body,
        title="Restore Admin_DB from S3 to MigrateDB",
        header="This is the header",
    )


@app.route("/create_form/", methods=["GET"])
@auth_required
def create_form():
    """called from main_menu.html
    dbengine has to be passed as an arg
    Value of dbengine has to match <info> data from mydb_config.py
    Example: "Postgres", "MongoDB", "MariaDB"...
    """
    if "dbengine" in request.args:
        print("DEBUG: create_form: dbengine: %s" % request.args["dbengine"])
        dbengine = request.args["dbengine"]
        return render_template(
            "general_form.html",
            dblabel=dbengine,
            image_list=mydb_config.info[dbengine]["images"],
        )
    else:
        message = "ERROR: create_form: url argument dbengine is incorrect. "
        message += "check index.html template"
        print(message)
        return "<h2>" + message + "</h2>"


@app.route("/created/", methods=["POST"])
@auth_required
def created():
    params = {}
    for item in request.form:
        params[item] = request.form[item].replace(";", "").replace("&", "").strip()
    params["username"] = session["username"]
    if mydb_config.FLASK_DEBUG == '1':
        print(f"DEBUG: mydb_views.created dbengine: {params['dbengine']}")
    if params["dbengine"] == "Postgres":
        result = postgres_util.create(params)
    elif params["dbengine"] == "MongoDB":
        result = mongodb_util.create_mongodb(params)
    elif params["dbengine"] == "MariaDB":
        result = mariadb_util.create(params)
    else:
        result = "Error: file=postgres_view, def=created(), "
        result += 'message="dbengine not set in general_form.html"'
    params["result"] = result
    return render_template("created.html", **params)


migrate_actions = ["migrate", "migrate_info", "migrate_list_s3", "migrate_backuplog"]
admin_actions = [
    "list_s3",
    "backup",
    "admin_metadata",
    "audit_db",
    "audit_mysql",
    "admin_delete",
    "restore",
    "connection",
    "services",
]


@app.route("/select_container/", methods=["GET"])
@auth_required
def select_container():
    """redirected from main_menu.html - general purpose menu for selecting a container
    from selected_container direct to <selected> and perform <dbaction>
    """
    action = request.args["dbaction"]
    if action in admin_actions:
        container_names = admin_db.list_container_names()
    elif action in migrate_actions:
        container_names = migrate_db.list_container_names()
    else:
        return render_template("404.html", title="404 Error")
    container_names.sort()
    if action == "list_s3":
        title = "View S3 Backups"
    elif action == "backup":
        title = "Backup Container Database"
    elif action == "restore":
        title = "Select Container to Restore from S3 Backup"
    elif action == "admin_metadata":
        title = "Select Container to get MetaData"
    elif action in ["audit_db"]:
        title = "Select Container to Audit"
    elif action in migrate_actions:
        title = "Select Container from MigrateDB"
    elif action == "admin_delete":
        title = "Select Container to Delete"
    else:
        title = "Select Service"
    return render_template(
        "select_item.html",
        dbaction=action,
        title=title,
        header="Select Container Name",
        placeholder="Container Name",
        items=container_names,
    )


@app.route("/selected/", methods=["GET"])
@auth_required
def selected():
    action = request.args["dbaction"]
    container_name = request.args["container_name"]
    if action == "backup":
        result = mydb_actions.user_backup(container_name)
        return render_template(
            "action_result.html",
            result=result,
            title="Container Backup",
            header=f"Backup Results for {container_name}",
        )
    elif action == "list_s3":
        backups = aws_util.list_s3(container_name, mydb_config.s3_prefix_prod)
        return render_template(
            "action_result.html",
            result=backups,
            title="S3 Backup Objects",
            header=container_name + " S3 Backup Objects",
        )
    elif action == "admin_metadata":
        json_data = admin_db.display_container_info(container_name)
        print(f"DEBUG {__file__}.admin_metadata\n{json_data}")
        return render_template(
            "action_result.html",
            result=json_data,
            title="MigrateDB Data",
            header=f"Meta data for {container_name}",
        )
    elif action == "restore":
        session["restore_from"] = container_name
        backup_prefixes = aws_util.parse_s3_backup_list(container_name, mydb_config.s3_prefix_prod)
        return render_template(
            "select_item.html",
            title="Select a S3 Backup",
            header="Select S3 Backup Prefix",
            placeholder="S3 backup prefix",
            items=backup_prefixes,
            dbaction="s3_select",
        )
    elif action == "s3_select":
        s3_url = request.args["container_name"]
        session["s3_url"] = s3_url
        print(f"DEBUG: restore s3_url: {s3_url}")
        container_names = admin_db.list_container_names()
        return render_template(
            "select_item.html",
            dbaction="restore_to",
            title="Select Target Container for Restore",
            header="Select Target Container",
            placeholder="Container Name",
            items=container_names,
        )
    elif action == "restore_to":
        target_container = request.args["container_name"]
        result = mydb_actions.restore(target_container, session["s3_url"])
        return render_template(
            "action_result.html",
            title="Restore Completed",
            header=f"Restored {session['restore_from']} to {target_container}\n from {session['s3_url']}",
            result=result,
        )
    elif action in admin_actions:
        return mydb_actions.admin_actions(action, request.args)
    elif action in migrate_actions:
        return mydb_actions.migrate_actions(action, request.args)
    else:
        return render_template("404.html", title="404 Error")


#  restart, delete, migrate
@app.route("/select_with_auth/", methods=["GET"])
@auth_required
def select_with_auth():
    """from main_menu.html general purpose menu to select something that
    requires auth. from selected_with_container direct to <selected_auth> and perform <dbaction>
    """
    action = request.args["dbaction"]
    container_names = admin_db.list_container_names()
    container_names.sort()
    if action == "restart":
        title = "Select Container to Restart"
    elif action == "delete":
        title = "Select Container to Delete"
    return render_template(
        "select_with_auth.html", title=title, dbaction=action, items=container_names
    )


@app.route("/selected_auth/", methods=["POST"])
@auth_required
def selected_auth():
    args = {}
    for arg_key in request.args.keys():
        args[arg_key] = request.args[arg_key]
    print(f"DEBUG: selected_auth: {args} {request.args.keys}")
    Name = request.form["Name"].replace(";", "").replace("&", "").strip()
    dbuser = request.form["dbuser"].replace(";", "").replace("&", "").strip()
    dbuserpass = request.form["dbuserpass"].replace(";", "").replace("&", "").strip()
    dbaction = request.form["dbaction"].replace(";", "").replace("&", "").strip()
    username = session["username"]
    if dbaction == "restart":
        result = mydb_actions.restart_con(Name, dbuser, dbuserpass, username)
        return render_template(
            "action_result.html",
            result=result,
            title="Container Restarted",
            header="Container " + Name + " Restarted",
        )
    elif dbaction == "delete":
        result = mydb_actions.auth_delete(Name, dbuser, dbuserpass, username)
        return render_template(
            "action_result.html",
            result=result,
            title="Container Deleted",
            header="Container " + Name + " Deleted",
        )


@app.route("/list_from_migrate/")
@auth_required
@admin_required
def list_from_migrate():
    (header, body) = migrate_db.display_active_containers()
    return render_template(
        "dblist.html", title="Containers from Migrate DB", dbheader=header, dbs=body
    )


def admin_help():
    body = """
MyDB administrators must be added to mydb_config.admins.
append admin commands to URL
/admin/help/   Your reading it.
/admin/session_info Display session variables
/admin/email_list Create JSON output of all users grouped by email
/admin/state/  Display all records in State table
/admin/list Display running containers
/admin/docker_ps Docker ps ouput
/admin/inspect?name=[container name]
/admin/volume_list/  List Docker Volumes
/admin/log/  Display all records from ActionLog table
/admin/info?[name=xx | cid=n]   Display Info data from containers table.
/admin/containers   Display summary from containers
/admin/data?cid=n  Display Docker inspect from AdminDB
/admin/update?cid=n&key=value&...  Update Info with new key: values
/admin/backup_audit[?name=xx | cid=x]  Display backup audit report for container
/admin_mode?mode=[on|off]
/admin/delete_container_state?cid=n  Only remove from State table
/admin/restore_admin_db Restore myd_admin from S3 to migrate_db
URL encoding tips:  Space: %20, @: %40"""

    return body


@app.route("/admin/<cmd>")
@auth_required
@admin_required
def admin(cmd):
    args = {}
    for arg_key in request.args.keys():
        args[arg_key] = request.args[arg_key]

    if cmd == "help":
        body = admin_help()
        title = "MyDB Administrative Features\n"
        return render_template("dblist.html", title=title, dbheader="", dbs=body)
    elif cmd == "session_info":
        return render_template("session.html", title="Session Variables")
    elif cmd == "restore":
        container_names = migrate_db.list_container_names()
        return render_template(
            "restore.html", title="Restore Database from Backup", items=container_names
        )
    elif cmd == "email_list":
        (header, body) = admin_db.display_email_list()
        return render_template(
            "dblist.html", title="List Users Email", dbheader=header, dbs=body
        )
    elif cmd == "state":
        (header, body) = admin_db.display_container_state()
        return render_template(
            "dblist.html", title="Admin DB State Table", dbheader=header, dbs=body
        )
    elif cmd == "list":
        (header, body) = admin_db.display_active_containers()
        return render_template(
            "dblist.html", title="Active Containers", dbheader=header, dbs=body
        )
    elif cmd == "containers":
        (header, body) = admin_db.display_containers()
        return render_template(
            "dblist.html", title="Containers Summary", dbheader=header, dbs=body
        )
    elif cmd == "inspect":
        body = "Container 'name' not found."
        if "name" in args:
            service_attrs = swarm_util.get_service(args["name"])
            if service_attrs:
                body = json.dumps(service_attrs, indent=4)
        return render_template(
            "dblist.html",
            title=f"Docker Inspect for {args['name']}",
            dbheader="",
            dbs=body,
        )
    elif cmd == "volume_list":
        header, body = swarm_util.display_volume_list()
        return render_template(
            "dblist.html", title="Docker Volumes", dbheader=header, dbs=body
        )
    elif cmd == "migrate_s3_prefix":
        """ return the s3 prefix for the most current backup"""
        if "name" in args:
            body = migrate_db.lastbackup_s3_prefix(args["name"])
        else:
            body = "Please tell me what the container name is. ?name=name"
        print(f"{__file__} cid: {args['name']}")
        return render_template(
            "dblist.html",
            title=f"S3 Prefix for last 'prod' backup of {args['name']}",
            dbs=body,
        )
    elif cmd == "backup_all":
        body = backup_util.backup_all()
        return render_template(
            "dblist.html", title="Backup All", dbheader="", dbs=body
        )
    elif cmd == "backup_audit":
        title = f"Backup AuditReport for prefix:{mydb_config.s3_prefix_prod}"
        if "name" in args:
            (header, body) = backup_util.backup_audit(args["name"])
        elif "cid" in args:
            (header, body) = backup_util.backup_audit(cid=args["cid"])
        else:
            (header, body) = backup_util.backup_audit()
        return render_template("dblist.html", title=title, dbheader=header, dbs=body)
    elif cmd == "log":
        (header, body) = admin_db.display_container_log()
        return render_template(
            "dblist.html", title="Admin DB Log", dbheader=header, dbs=body
        )
    elif cmd == "data":
        data = admin_db.get_container_data(args["name"], cid)
        body = json.dumps(data, indent=4)
        title = "Container Inspect from admindb"
        return render_template("dblist.html", title=title, dbheader="", dbs=body)
    elif cmd == "info":
        body = admin_db.display_container_info(args["name"], cid)
        title = "Container Info "  # for %s' % body['Name']
        return render_template("dblist.html", title=title, dbheader="", dbs=body)
    elif cmd == "update":
        info = {}
        for item in request.args.keys():
            if "cid" != item:
                info[item] = request.args[item]
        if "cid" in request.args and len(info.keys()) > 0:
            admin_db.update_container_info(request.args["cid"], info)
            return "Updated Info\n" + json.dumps(info, indent=4)
        else:
            return "DEBUG: admin-update: No URL arguments"
    elif cmd == "restore_admin_db":
        result = postgres_util.restore_admin_db()
        title = "Restore mydb_admin from S3 to migrate_db"
        return render_template(
            "dblist.html", title=title, dbheader="pg_dump output", dbs=result
        )
    elif cmd == "services":
        header, body = swarm_util.display_services()
        title = "MyDB Admin Services"
        return render_template("dblist.html", title=title, dbheader=header, dbs=body)
    elif cmd == "delete_container_state":
        """ Only removes the Metadata from admin_db """
        admin_db.delete_container_state(request.args["cid"])
        return "Administratively removed meta data from admin_db"
    else:
        return "incorect admin URL" + cmd


@app.route("/admin_mode/")
@auth_required
def admin_mode():
    if "mode" in request.args:
        title = "MyDB Admin Mode"
        if request.args["mode"] is None:
            body = "/admin/admin_mode must speicify the mode to be set.\n"
            body += "/admin/admin_mode?mode=[on|off]\n"
            return render_template("dblist.html", title=title, dbheader="", dbs=body)
        elif request.args["mode"] not in ["on", "off"]:
            body = "/admin/admin_mode must speicify the mode to be set.\n"
            body += "/admin/admin_mode?mode=[on|off]\n"
            return render_template("dblist.html", title=title, dbheader="", dbs=body)
        elif request.args["mode"] == "on" and session["username"] in mydb_config.admins:
            session["admin_user"] = True
        elif request.args["mode"] == "off":
            session["admin_user"] = False
        body = f"Admin Mode = {session['admin_user']}"
        dbheader = f"Set MyDB Admin Mode"
        return render_template("dblist.html", title=title, dbheader=dbheader, dbs=body)
    return render_template("admin_mode.html")


@app.route("/cron/<cmd>", methods=["GET"])
def cron(cmd):
    header = request.headers.get("DB4SCI-Task-Token")
    print(f"cron: cmd: {cmd} Header: {header}")
    if header != mydb_config.DB4SCI_TASK_TOKEN:
        return "<h2> invalid task token</h2>"
    if cmd == "backup_all":
        body = backup_util.backup_all()
        return body, 204  # No Content
    else:
        return '<h2> invalid command</h2>', 400  # Bad Request


@app.route("/doc_page/")
def doc_page():
    doc_name = request.args["doc"]
    doc_name += ".html"
    return render_template(doc_name)
