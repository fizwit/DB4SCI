import copy
import datetime
import json
from argparse import ArgumentParser

from sqlalchemy import create_engine, desc
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from . import mydb_config
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
    """
    from . import models

    Base.metadata.create_all(bind=engine)
    print("Initialized production database")


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
    READ get_container_state(con_name, c_id)
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


def get_container_state(Name=None, c_id=None):
    """Get current state of a container
    returns (state, c_id)
    None if not found
    """
    if c_id is not None:
        state_info = ContainerState.query.filter(ContainerState.c_id == c_id).first()
    elif Name:
        state_info = ContainerState.query.filter(ContainerState.name == Name).first()
    else:
        state_info = None
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
    u = ContainerState.query.filter(ContainerState.c_id == c_id).delete()
    db_session.commit()
    description = f"deleted CID {c_id} by user admin"
    add_container_log(c_id, "unknown", "delete-state", description)


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
        data = get_container_data("", c_id=state.c_id)
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


def add_service(service, params):
    """Add new container to admin database
    input: Docker inspect from container
    Info block is added to Docker Inspect and stored as JSONB
    in the <data> column of table containers.
    """
    Info = copy.deepcopy(params)
    Info["State"] = "running"
    # Info["Port"] = service.attrs["Endpoint"]["Ports"][0]["TargetPort"]
    Info["PublishedPort"] = service.attrs["Endpoint"]["Ports"][0]["PublishedPort"]
    Info["CreatedAt"] = service.attrs["CreatedAt"]
    Info["LastState"] = "created"
    print(f"DEBUG: {__file__}.add_service: {json.dumps(Info, indent=4)}")

    # Convert service.attrs to plain dict and add our custom fields
    data = dict(service.attrs)
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
    u = Containers.query.filter(Containers.id == id).delete()
    db_session.commit()


def get_container_data(con_name, c_id=None):
    """return list of dicts
    list of <data> field (JSONB) from containers table as dict
    data field contains 'Info'
    """
    if c_id:
        result = Containers.query.filter(Containers.id == c_id).all()
    else:
        result = Containers.query.filter(
            Containers.data["Info"]["Name"].astext == con_name
        ).all()
    if isinstance(result, list) and len(result) > 0:
        retrieved_data = result[0].data
        print(
            f"DEBUG: {__file__}.get_container_data retrieved keys: {retrieved_data.keys()}"
        )
        print(
            f"DEBUG: {__file__}.get_container_data 'Info' in retrieved: {'Info' in retrieved_data}"
        )
        if "Info" not in retrieved_data:
            print(
                f"WARNING: 'Info' key missing from data for c_id={result[0].id}, name={con_name}"
            )
            print(f"Available keys: {list(retrieved_data.keys())}")
        return retrieved_data
    else:
        return []


def get_container_info(Name) -> tuple:
    """get container info data
    return tuple
    dbengine:  'Postgres', 'MariaDB', 'MongoDB', 'Neo4j' etc
    """
    state = get_container_state(Name=Name)
    if state:
        data = get_container_data("", c_id=state.c_id)
        c_id = state.c_id
        info = data["Info"]
        # Standard key is dbengine
        dbengine = info.get("dbengine", "")
    else:
        c_id = None
        info = {}
    return (c_id, info)


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


def display_container_info(con_name, c_id=None):
    """Return pretty json of 'Info' from container table"""
    if con_name:
        state = get_container_state(Name=con_name)
    if state:
        c_id = state.c_id
        data = get_container_data("", c_id=c_id)
        return json.dumps(data["Info"], indent=4)
    else:
        return f"Meta data not found for {con_name}"


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
    with open("user_email_data.json", "w") as file:
        json.dump(emails, file, indent=4)
    return ("User list JSON", body)


def display_active_containers():
    """Return summary of running containers.
    This should be used from the GUI
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
        data = get_container_data("", c_id)
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


if __name__ == "__main__":
    parser = ArgumentParser(
        description="unit test for admin_db module",
        usage="%(prog)s [options] module_name",
    )
    parser.add_argument(
        "--info",
        action="store",
        dest="con_name",
        help="display the info field for a container",
    )
    parser.add_argument(
        "--state",
        action="store_true",
        dest="state",
        help='List the "status" for all active containers',
    )
    parser.add_argument(
        "--active",
        action="store_true",
        dest="active",
        help="Display all active containers, from admin dB",
    )
    parser.add_argument(
        "--show_event_logs",
        action="store_true",
        dest="show_event_logs",
        help="Show MyDB event log",
    )
    results = parser.parse_args()

    if results.con_name:
        data = get_container_data(results.con_name)
        if len(data) == 0:
            print(f"{results.con_name} not found in state database")
        else:
            info = data["Info"]
            print(f"Container DB Info[] for {results.con_name}")
            for k in info.keys():
                print("%-20s: %s" % (k, info[k]))
    elif results.state:
        (header, body) = display_container_state()
        print(header)
        print(body)
    elif results.active:
        (header, body) = display_active_containers()
        print(header)
        print(body)
    elif results.show_event_logs:
        (header, body) = display_container_log()
        print("MyDB Event Log")
        print(header)
        print(body)
