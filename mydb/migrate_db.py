import json
from .format_fill import format_fill
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy import desc
from . import mydb_config
from .human import human_uptime

# Import Base from admin_db (where models are registered)
from .admin_db import Base

# Import models - they'll be queried through our session
from .models import Containers, ContainerState, Backups

# Create migrate engine
MIGRATE_URI = mydb_config.SQLALCHEMY_MIGRATE_URI

# Only create engine if URI is configured
migrate_engine = None
MigrateSessionFactory = None
db_session = None

if MIGRATE_URI:
    # Create engine with connection pool settings
    # pool_pre_ping: Test connections before using them to avoid stale connections
    # pool_recycle: Recycle connections after 3600 seconds (1 hour)
    migrate_engine = create_engine(
        MIGRATE_URI,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    print(f"Migrate engine: {MIGRATE_URI}")

    # Create session factory
    MigrateSessionFactory = sessionmaker(
        autocommit=False, autoflush=False, bind=migrate_engine
    )

    # Session for migrate database
    db_session = scoped_session(MigrateSessionFactory)
else:
    print("Migrate database not configured (SQLALCHEMY_MIGRATE_URI not set)")


def init_db():
    """
    Initialize migrate database schema.
    """
    if not migrate_engine:
        print("Cannot initialize migrate database - not configured")
        return
    from . import models

    Base.metadata.create_all(bind=migrate_engine)
    print("Initialized migrate database")


def list_container_names():
    """Return python list of all containers in container table
    list of tuples
    """
    if not db_session:
        raise ValueError(
            "Migrate database not configured. Set SQLALCHEMY_MIGRATE_URI environment variable."
        )
    containers = []
    result = db_session.query(ContainerState).all()
    for state in result:
        containers.append(state.name)
    return containers


def get_container_state(con_name=None, c_id=None):
    """
    Get current state of a container
    returns (state, c_id)
    None if not found
    """
    if not db_session:
        raise ValueError("Migrate database not configured.")
    if c_id is not None:
        state_info = (
            db_session.query(ContainerState).filter(ContainerState.c_id == c_id).first()
        )
    elif con_name:
        state_info = (
            db_session.query(ContainerState)
            .filter(ContainerState.name == con_name)
            .first()
        )
    else:
        state_info = None
    return state_info


def display_active_containers():
    """Return summary of running containers.
    This should be used for the GUI
    hacked for migrate to additional info about accounts
    """
    widths = [3, 24, 15, 15, 24, 30, 6, 30, 25, 7]
    header_text = (
        "CID",
        "Container",
        "POSTGRES_USER",
        "DB_USER",
        "Owner",
        "Contact",
        "Port",
        "Image",
        "Created",
        "PW Match",
    )
    header = format_fill("left", header_text, widths)

    # Get list of active containers
    containers = []
    state_info = db_session.query(ContainerState).all()
    for state in state_info:
        containers.append([state.c_id, state.name])

    cid_list = [containers[c_id][0] for c_id in range(len(containers))]
    body = ""
    counter = 0
    for c_id in cid_list:
        data = get_container_data("", c_id)
        info = data["Info"]
        started = data["State"]["StartedAt"]
        human = human_uptime(started)
        postgres_user = info.get("POSTGRES_USER", "na")
        db_user = info.get("DB_USER", "na")
        postgres_pass = info.get("POSTGRES_PASSWORD", None)
        dbuserpass = info.get("dbuserpass", None)
        pw_match = "NA"
        if postgres_pass and dbuserpass:
            if postgres_pass == dbuserpass:
                pw_match = "yes"
            else:
                pw_match = "no"
        elif postgres_pass:
            pw_match = "PGU"
        elif dbuserpass:
            pw_match = "no PG!"
        image = info.get("Image", "NA")
        row = (
            str(c_id),
            info["Name"],
            postgres_user,
            db_user,
            info["OWNER"],
            info["CONTACT"],
            info["Port"],
            image,
            human,
            pw_match,
        )
        body += format_fill("left", row, widths)
        counter += 1
    body += f"\nTotal Containers {counter}\n"
    return (header, body)


def get_container_data(con_name, c_id=None):
    """return list of dicts
    list of <data> field (JSONB) from containers table as dict
    data field contains 'Info'
    """
    if not db_session:
        raise ValueError("Migrate database not configured.")
    if c_id:
        result = db_session.query(Containers).filter(Containers.id == c_id).all()
    else:
        result = (
            db_session.query(Containers)
            .filter(Containers.data["Name"].astext == "/" + con_name)
            .all()
        )
    if isinstance(result, list) and len(result) > 0:
        return result[0].data
    else:
        return []


def display_container_info(con_name, c_id=None):
    """Return pretty json of 'Info' from container table"""
    if con_name:
        state = get_container_state(con_name=con_name)
        c_id = state.c_id
    data = get_container_data("", c_id=c_id)
    return json.dumps(data["Info"], indent=4)


def display_containers():
    """Return summary from containers table
    Containers table has every container ever created, Container Names can be
    repeated.
    """
    if not db_session:
        return ("", "Migrate database not configured\n")
    result = db_session.query(Containers).all()
    dis_format = "%3s %-22s %-15s %-22s %-30s %-8s %-6s %-30s %s\n"
    header = dis_format % (
        "CID",
        "Container",
        "Username",
        "Owner",
        "Contact",
        "Status",
        "Port",
        "Image",
        "Created",
    )
    body = ""
    for row in result:
        cid = row.id
        info = row.data["Info"]
        started = row.data["State"]["StartedAt"]
        human = human_uptime(started)
        user = "NA"
        if "POSTGRES_USER" in info:
            user = info["POSTGRES_USER"]
        elif "DB_USER" in info:
            user = info["DB_USER"]
        image = "NA"
        if "Image" in info:
            image = info["Image"]
        body += dis_format % (
            str(cid),
            info["Name"],
            user,
            info["OWNER"],
            info["CONTACT"],
            info["State"],
            info["Port"],
            image,
            human,
        )
    return (header, body)


def format_json(dict):
    """Custom format JSON to save space and make human readable
    and JSON
    """
    nl = "\n"
    body = "{\n"
    ecounter = 1
    elast = len(dict.keys())
    for data in dict.keys():
        body += f'"{data}": {{"user": "{dict[data]["user"]}",{nl}'
        body += '    "containers": [\n'
        last = len(dict[data]["containers"])
        ccounter = 1
        for container in dict[data]["containers"]:
            if ccounter == last:
                body += f'        ["{container[0]}", "{container[1]}", "{container[2]}"]{nl}'
            else:
                body += f'        ["{container[0]}", "{container[1]}", "{container[2]}"],{nl}'
            ccounter += 1
        if elast == ecounter:
            body += "        ]\n    }\n"
        else:
            body += "        ]\n    },\n"
        ecounter += 1
    body += "}\n"
    return body


def display_email_list():
    """create list of users email and database names
    Group data by email, so users only get one notice
    """
    if not db_session:
        return ("", "Migrate database not configured\n")
    # Get list of active containers
    containers = []
    state_info = db_session.query(ContainerState).all()
    for state in state_info:
        containers.append([state.c_id, state.name])

    cid_list = [containers[c_id][0] for c_id in range(len(containers))]
    emails = {}
    for c_id in cid_list:
        data = get_container_data("", c_id)
        info = data["Info"]
        started = data["State"]["StartedAt"]
        started_h = human_uptime(started)
        if info["CONTACT"] not in emails:
            emails[info["CONTACT"]] = {"user": info["OWNER"], "containers": []}
        emails[info["CONTACT"]]["containers"].append(
            [info["Name"], info["Image"], started_h]
        )
    body = format_json(emails)
    file_name = "migrate_email_data.json"
    with open(file_name, "w") as file:
        json.dump(emails, file, indent=4)
    return (f"JSON data written to {file_name}", body)


def backup_lastlog(c_id, tail=None):
    """Query backup log for the last two log messages for a container"""
    if not db_session:
        raise ValueError("Migrate database not configured.")
    limit = 2 if not tail else tail
    result = (
        db_session.query(Backups)
        .filter(Backups.c_id == c_id)
        .order_by(desc(Backups.ts))
        .limit(limit)
        .all()
    )
    return result


def lastbackup_s3_prefix(name):
    """Query backup log for the most recent backup for a container"""
    if not db_session:
        return "Migrate database not configured."
    result = (
        db_session.query(Backups)
        .filter(Backups.name == name)
        .order_by(desc(Backups.ts))
        .first()
    )
    if result:
        return result.url
    else:
        return f"No backup found for container c_id: {name}"


def mirgrate(info):
    """Migrate MariaDB data"""
    return "Not implemented yet"
