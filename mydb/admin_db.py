import copy
import datetime
import json
import sys
from docker.types import swarm
from sqlalchemy import create_engine, desc
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from . import mydb_config
from . import swarm_util
from .format_fill import format_fill
from .human import human_uptime

# Create production engine
PROD_URI = mydb_config.SQLALCHEMY_ADMIN_URI
print(f"Production engine URI: {PROD_URI}")

# Production engine with connection pool settings
# pool_pre_ping: Test connections before using them to avoid stale connections
# pool_recycle: Recycle connections after 3600 seconds (1 hour)
engine = create_engine(
    PROD_URI,
    pool_pre_ping=True,
    pool_recycle=3600,
)
print(f"Production engine: {PROD_URI}")

# Create session factory
SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Default session (production)
db_session = scoped_session(SessionFactory)

Base = declarative_base()
Base.query = db_session.query_property()

from .models import ActionLog, Backups, Containers, ContainerState, Labels


def init_db():
    """
    Initialize database schema.

    The admin database may not be reachable at startup (e.g. the service is
    still coming up, or DNS for its host is not resolvable yet). Rather than
    letting an OperationalError crash the process and trigger an infinite
    container restart loop, log a clear warning and start in a degraded state.
    """
    from . import models

    try:
        Base.metadata.create_all(bind=engine)
        if mydb_config.FLASK_DEBUG:
            print("Initialized production database")
        state_info = get_container_state(Name="admin_db")
        if state_info is None:
            """Add admin_db service if not present"""
            params = {
                'backup_freq': 'Daily',
                'Port': str(mydb_config.base_port - 1),
                'Name': 'admin_db',
                'dbname': 'admin_db',
                'dbengine': 'Postgres',
                'dbuser': mydb_config.admins[0],
                'username': mydb_config.admins[0],
                'description': "DB4SCI admin database",
                'labels': {
                    'owner': mydb_config.supportPerson,
                    'contact': mydb_config.supportEmail,
                }
            }
            service_attrs = swarm_util.get_service("mydb_admin_db")
            if service_attrs:
                image = service_attrs['Spec']['TaskTemplate']['ContainerSpec']['Image'].split('@')[0]
                params['image'] = image
                add_service(service_attrs, params)
    except OperationalError as err:
        print(
            f"WARNING: could not connect to the admin database at {PROD_URI!r}: "
            f"{err.orig if err.orig is not None else err}\n"
            "Starting without an initialized admin database. Verify the "
            "'mydb_admin_db' service is running and reachable, then restart."
        )
        sys.exit(1)


"""ActionLog CRUD
    Log all DBaas Container events: [create, delete, restart, backup,
     maintenance]
    CREATE log messages
    READ display_containerlog()
"""


def add_container_log(c_id, name, action, description, ts=None):
    """Log event to table ActionLog
    Note: ts should be a auto fill field with current time stamp,
    but in order to generate log messages with correct histoical
    times the field has to be manually populated.
    ts: type datetime
    """
    if not ts:
        ts = datetime.datetime.now()
    u = ActionLog(c_id=c_id, name=name, action=action, description=description, ts=ts)
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)


def display_container_log(c_id=None, limit=None):
    """Return list of log messages
    filter by name or c_id
    limit number of rows returned
    """
    if c_id:
        result = ActionLog.query.filter(Containers.id == c_id).all()
    else:
        result = ActionLog.query.order_by(ActionLog.id.desc()).all()
    header = "%-20s %-30s %-30s %s\n" % ("TimeStamp", "Name", "Action", "Description")
    if not limit:
        limit = len(result)
    message = ""
    for row in result[0:limit]:
        timestamp = row.ts.strftime("%Y-%m-%d %H:%M:%S")
        message += "%-20s %-30s %-30s %s\n" % (
            timestamp,
            row.name,
            row.action,
            row.description,
        )
    return (header, message)


"""Container State CRUD
Container State table manages active containers. New records are added when
containers are created. Records are deleted when the container is deleted.
    CREATE add_container_state()
    READ get_container_state(con_name)
    UPDATE update_container_state()
    DELETE delete_container_state():
    Note: Docker container names begin with a backslash '\' data['Name']
    retains the backslah from Docker. But the slash is removed for all other
    Tables which use 'Name' as a field.
"""


def add_container_state(c_id, Info, who=None):
    """Add new container to State table."""
    if not who:
        who = "DBaaS"
    u = ContainerState(
        c_id=c_id,
        name=Info["Name"],
        state=Info["State"],
        last_state="created",
        observerd=Info["State"],
        changed_by=who,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)


def list_container_names():
    """Return python list of all containers in container table
    list of tuples
    """
    containers = []
    result = ContainerState.query.all()
    for state in result:
        containers.append(state.name)
    return containers


def get_container_state(Name=None):
    """Get current state of a container from 'state' table
    returns None if not found
    """
    state_info = ContainerState.query.filter(ContainerState.name == Name).first()
    return state_info


def update_container_state(c_id, state, who=None):
    """Change state of container"""
    if not who:
        who = "DBaaS"
    state_info = ContainerState.query.filter(ContainerState.c_id == c_id).first()
    a = ContainerState.query.filter(ContainerState.c_id == c_id).update(
        {
            "state": state,
            "last_state": state_info.state,
            "changed_by": who,
            "ts": datetime.datetime.now(),
        }
    )
    db_session.commit()
    add_container_log(
        c_id, state_info.name, "change state to " + state, "updated by DBaaS"
    )


def delete_container_state(c_id):
    """Delete record from Container_State table.
    Deleted Containers are not tracked in Container State
    """
    u = ContainerState.query.filter(ContainerState.c_id == int(c_id)).delete()
    db_session.commit()
    if u == 0:
        print(f"WARNING: delete_container_state -no ContainerState row found for CID {c_id}; nothing deleted")
        return u
    description = f"deleted CID {c_id} by user admin"
    add_container_log(c_id, "Admin", "delete-state", description)
    return u

def list_containers():
    """Return python list of all containers in container table
    list of tuples
    """
    containers = []
    result = Containers.query.all()
    for state in result:
        containers.append([state.id, state.name])
    return containers


def list_active_containers():
    """Return python list of all containers in state table
    list of tuples
    """
    containers = []
    state_info = ContainerState.query.all()
    for state in state_info:
        containers.append([state.c_id, state.name])
    return containers


def get_max_port():
    """Return the next available port number (highest used port + 1)

    Queries the admin database for all active containers and finds the
    highest port in use, then returns the next available port number.

    Returns:
        int: Next available port number

    Usage:
        params["Port"] = admin_db.get_max_port()
    """
    ports = [mydb_config.base_port]

    # Get all active containers from admin database
    state_info = ContainerState.query.all()

    for state in state_info:
        data = get_container_data(state.c_id)
        if data and "Info" in data and "Port" in data["Info"]:
            try:
                ports.append(int(data["Info"]["Port"]))
            except (ValueError, TypeError):
                # Skip if port is not a valid integer
                print(f"Warning: Invalid port for container {state.name}")
                continue

    return max(ports) + 1


def display_container_state():
    """List container state for all containers in Container State table"""
    fmtstring = "%4s %-30s %-12s %-12s %-15s %s\n"
    header = fmtstring % ("ID", "Name", "State", "Last", "Changed By", "TimeStamp")
    state_info = ContainerState.query.all()
    message = ""
    for state in state_info:
        if isinstance(state.ts, datetime.datetime):
            TS = state.ts.strftime("%Y-%m-%d %H:%M:%S")
        else:
            TS = ""
        outstring = fmtstring % (
            str(state.c_id),
            state.name,
            state.state,
            state.last_state,
            state.changed_by,
            TS,
        )
        message += outstring
    return header, message


"""Containers CRUD
Container table manages docker inspect <data> for containers. New records
are added when containers are created. Container <data> records are never
deleted. Data from 'Labels' can be modified; Example: Backup_freq.
Relation between <id> and <c_id between all other tables.
     CREATE add_container()
     READ get_container_data()
     UPDATE - update_container_info(c_id, info_data):
     DELETE - needed for mongodb and this is handy to use for test
              cases.
"""


def add_service(service_attrs, params):
    """Add new container to admin database
    input: Docker inspect attributes (dict) from container
    Info block is added to Docker Inspect and stored as JSONB
    in the <data> column of table containers.
    """
    Info = copy.deepcopy(params)
    Info["State"] = "running"
    # Info["Port"] = service.attrs["Endpoint"]["Ports"][0]["TargetPort"]
    Info["PublishedPort"] = service_attrs["Endpoint"]["Ports"][0]["PublishedPort"]
    Info["CreatedAt"] = service_attrs["CreatedAt"]
    Info["LastState"] = "created"
    print(f"DEBUG: {__file__}.add_service: {json.dumps(Info, indent=4)}")

    # Convert service.attrs to plain dict and add our custom fields
    data = dict(service_attrs)
    data["Info"] = Info
    u = Containers(data=data, name=Info["Name"])
    flag_modified(u, "data")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    add_container_state(u.id, Info)
    return u.id


def delete_container(id):
    """Delete record from Container table.
    Remove from container_state also
    """
    delete_container_state(id)
    u = Containers.query.filter(Containers.id == int(id)).delete()
    db_session.commit()


def get_container_data(c_id):
    """return 'data' field from containers table
    <c_id> Container ID must be int
    """
    result = Containers.query.filter(Containers.id == c_id).first()
    if result:
        return result.data
    else:
        return None


def update_container_info(c_id, info_data, who=None):
    """Update container info data. <data> is JSONB.
    <data['Info']> holds mutable data.
    <info_data> type: dict
    return modified container <data>
    """
    if not who:
        who = "DBaaS"
    result = Containers.query.filter(Containers.id == c_id).one()
    result.data["Info"].update(info_data)
    a = Containers.query.filter(Containers.id == c_id).update({"data": result.data})
    db_session.commit()
    add_container_log(
        c_id,
        result.data["Info"]["Name"],
        action="update info cid=" + str(c_id),
        description="update from DBaaS",
    )
    return result.data


def display_container_info(container_name, cid):
    """Return pretty json of 'Info' from container table"""
    if container_name:
        state = get_container_state(container_name)
        if state:
            cid = state.c_id
        else:
            return f"No data for {container_name} from get_container_state"
    data = get_container_data(cid)
    if data:
        return json.dumps(data["Info"], indent=4)
    else:
        return f"Meta data not found for container_name: {container_name} c_id: {cid}"


def display_containers():
    """Return summary from containers table
    Containers table has every container ever created, Container Names can be
    repeated.
    """
    result = Containers.query.all()
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
                body += f'        ["{container[0]}", "{container[1]}"]{nl}'
            else:
                body += f'        ["{container[0]}", "{container[1]}"],{nl}'
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
    active = list_active_containers()
    cid_list = [active[c_id][0] for c_id in range(len(active))]
    emails = {}
    for c_id in cid_list:
        data = get_container_data(c_id)
        info = data["Info"]
        started = data["State"]["StartedAt"]
        started_h = human_uptime(started)
        if info["CONTACT"] not in emails:
            emails[info["CONTACT"]] = {"user": info["OWNER"], "containers": []}
        emails[info["CONTACT"]]["containers"].append(
            [info["Name"], info["Image"], started_h]
        )
    body = format_json(emails)
    with open("user_email_data.json", "w") as file:
        json.dump(emails, file, indent=4)
    return ("User list JSON", body)


def display_active_containers():
    """Return summary of running containers.
    called from mydb_views
    """
    widths = (3, 24, 15, 24, 30, 6, 30, 25)
    header_text = (
        "CID",
        "Container",
        "Username",
        "Owner",
        "Contact",
        "Port",
        "Image",
        "Created",
    )
    header = format_fill("left", header_text, widths)
    active = list_active_containers()
    cid_list = [active[c_id][0] for c_id in range(len(active))]
    body = ""
    counter = 0
    for c_id in cid_list:
        data = get_container_data(c_id)
        print(f"data: {data}")
        info = data["Info"]
        started = data["CreatedAt"]
        human = human_uptime(started)
        user = info["dbuser"]
        image = info.get("image", "NA")
        row = (
            str(c_id),
            info["Name"],
            user,
            info["labels"]["owner"],
            info["labels"]["contact"],
            info["Port"],
            image,
            human,
        )
        body += format_fill("left", row, widths)
        counter += 1
    body += f"\nTotal Containers {counter}\n"
    return (header, body)


def backup_log(c_id, name, state, backup_id, backup_type, url, command, err_msg):
    """Log event to backup log.  Every backup should be logged
    <created> TIMESTAMP
    <duration> integer
    """
    ts = datetime.datetime.now()
    u = Backups(
        c_id=c_id,
        name=name,
        state=state,
        backup_id=backup_id,
        backup_type=backup_type,
        url=url,
        command=command,
        err_msg=err_msg[:100],
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)


def get_lastbackup_from_log(name):
    """Query backup log for the most recent backup for a container"""
    result = (
        Backups.query.filter(Backups.name == name).order_by(desc(Backups.ts)).first()
    )
    if result:
        return result.url
    else:
        return f"No backup found for container: {name}"


def backup_lastlog(c_id, tail=None):
    """Query backup log for the last two log messages for a container"""
    limit = 2 if not tail else tail
    result = (
        Backups.query.filter(Backups.c_id == c_id)
        .order_by(desc(Backups.ts))
        .limit(limit)
    )
    # if len(result) != 2:
    #    print('Error: no records for: %d' % c_id)
    #    return None
    return result


def backup_taillog(c_id, tail=None):
    """Query backup log for the last two log messages for a container"""
    limit = 2 if not tail else tail
    result = (
        Backups.query.filter(Backups.c_id == c_id).order_by(Backups.ts).limit(limit)
    )
    db_session.commit()
    print(f"DEBUG: {__file__}.backup_taillog {result}")
    return result
