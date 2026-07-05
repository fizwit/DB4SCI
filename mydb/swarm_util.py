"""
Docker Swarm utility functions for MyDB

This module contains functions for managing Docker Swarm services.
"""

import json
import sys
import time

import docker
from docker.errors import APIError, NotFound
from docker.types import ConfigReference, EndpointSpec, Mount, RestartPolicy

from . import admin_db, mydb_config
from .human import human_uptime
from .send_mail import send_mail

# Initialize Docker client
client = docker.from_env()


def get_service(service_name):
    try:
        service = client.services.get(service_name)
    except NotFound:
        return None
    return service.attrs

def display_volume_list():
    volumes = volume_list()
    header = "{:<40} {:<10} {}".format("Volume", "Driver", "Created")
    body = ""
    for volume in volumes:
        if "mydb" in volume["name"]:
            up_time = human_uptime(volume["created"])
            body += f"{volume['name']:<40} {volume['driver']:<10} {up_time}\n"
    return header, body


def volume_list():
    """list all volumes using docker system df for size information"""
    volumes = client.volumes.list()

    volume_info = []
    for volume in volumes:
        volume_info.append(
            {
                "name": volume.attrs["Name"],
                "driver": volume.attrs["Driver"],
                "created": volume.attrs["CreatedAt"],
            }
        )
    return volume_info


def create_docker_volume(vname):
    """create a volume if it does not exist
    Returns: (volume_id, error) tuple
        - If successful: (volume_id, None)
        - If error: (None, error_message)
    """
    try:
        volume = client.volumes.get(vname)
        return volume.id, None  # Volume already exists, no error
    except docker.errors.NotFound:
        try:
            volume = client.volumes.create(vname)
            return volume.id, None  # Volume created successfully, no error
        except docker.errors.APIError as e:
            return None, f"Error creating volume: {e}"
    except docker.errors.APIError as e:
        return None, f"Error checking volume: {e}"
    except Exception as e:
        return None, f"Unexpected error: {e}"


def volume_remove(vname):
    """Remove a docker volume
    volume remove typically fails until the service if fully removed.
    Try to remove for a few times before giving up"""
    try:
        volume = client.volumes.get(vname)
    except NotFound:
        return f"Docker volume {vname} not found"
    count = 0
    while count < 5:
        try:
            volume.remove()
            mesg = f"Docker Volume {vname} removed."
            break
        except APIError as e:
            print(f"Error volume_remove: {vname}: {e}, tring again")
            time.sleep(2)
            count += 1
            mesg = f"Issues removing {vname}. Errors {e}"
    return mesg


def create_config(params, config, target_path=None):
    """Create Docker Swarm config for service initialization

    Args:
        params: Dictionary containing config_name and other parameters
        config: String content of the config file
        target_path: Optional path where config should be mounted in container.
                    Defaults to /docker-entrypoint-initdb.d/init.sql for PostgreSQL

    Returns:
        List of ConfigReference objects or None on error
    """
    if target_path is None:
        target_path = "/docker-entrypoint-initdb.d/init.sql"

    try:
        config_obj = client.configs.create(
            name=params["config_name"], data=config.encode("utf-8")
        )
    except docker.errors.APIError as e:
        print(f"create_config: error: {e}", file=sys.stderr)
        return None
    params["config_id"] = config_obj.id
    config_ref = [
        ConfigReference(
            config_id=config_obj.id,
            config_name=params["config_name"],
            filename=target_path,
            uid="999",
            gid="999",
            mode=0o555,
        )
    ]
    return config_ref


def start_service(params, config_ref):
    # Create docker service
    params_json = json.dumps(params, indent=4)
    print(f"====DEBUG: swarm_util.start_service: params: {params_json}")
    service = client.services.create(
        image=params["image"],
        name=params["service_name"],
        user=params["service_user"],
        env=params["env"],
        mounts=[
            Mount(
                target=params["mapped_db_vol"],
                source=f"{params['volume_name']}",
                type="volume",
            ),
            {  # ← Use dict for tmpfs
                "Target": "/dev/shm",
                "Type": "tmpfs",
                "TmpfsOptions": {"SizeBytes": 1073741824, "Mode": 1777},
            },
        ],
        configs=config_ref,
        endpoint_spec=EndpointSpec(ports={int(params["Port"]): params["default_port"]}),
        restart_policy=RestartPolicy(condition="any"),
        labels=params["labels"],
    )

    # Wait for service to have running tasks
    time.sleep(1)
    timeout = 30  # seconds
    start_time = time.time()

    while time.time() - start_time < timeout:
        service.reload()
        tasks = service.tasks()
        if tasks:
            task = tasks[0]
            if task["Status"]["State"] == "running":
                print(f"Service {params['Name']} is running")
                c_id = admin_db.add_service(service.attrs, params)
                return service, "Service Started"
            elif task["Status"]["State"] in ["failed", "shutdown", "rejected"]:
                error_msg = task["Status"].get("Err", "Unknown error")
                send_mail(
                    "MyDB: service failed to start",
                    f"Service {params['Name']} failed: {error_msg}",
                    mydb_config.supportEmail,
                )
                return (
                    None,
                    f"Service failed to start. State: {task['Status']['State']}, Error: {error_msg}",
                )
        time.sleep(0.5)

    return None, f"Service did not start within {timeout} seconds."


def stop_remove(service_name):
    """Stop and remove a docker swarm service
    In Swarm, services don't need to be "stopped" - removing them
    automatically stops all tasks.

    Args:
        service_name: Name of the service to remove

    Returns:
        True if successful
        Error message string if failed

    Note:
        This should not be accessed directly, but from kill_service()
        kill_service will cleanup the admin_db
    """
    try:
        service = client.services.get(service_name)
    except docker.errors.NotFound:
        msg = f"Error: Service not found: {service_name}"
        print(msg)
        return msg
    except docker.errors.APIError as e:
        msg = f"Error: API error getting service {service_name}: {e}"
        print(msg)
        return msg

    try:
        service.remove()
        msg = f"Service {service_name} removed successfully"
        return msg
    except docker.errors.APIError as e:
        msg = f"Error: removing service {service_name}: {e}"
        print(msg)
        return msg


def restart_service(name):
    """Restart a Docker Swarm service by forcing an update

    In Docker Swarm, there's no direct "restart" command. Instead,
    we use service.update(force_update=True) which recreates the
    service's tasks (containers), effectively restarting them.

    This is important for database configuration changes that require
    a restart to take effect (e.g., ALTER SYSTEM in PostgreSQL,
    SET PERSIST in MariaDB).

    Args:
        name: Container name (not service name - we'll look up the service)

    Returns:
        String message indicating success or error
    """
    state_info = admin_db.get_container_state(name)
    if state_info is None:
        return f"Error: Container '{name}' not found in Admin DB"

    data = admin_db.get_container_data(state_info.c_id)
    if "Info" not in data or "service_name" not in data["Info"]:
        return f"Error: Service name not found for container '{name}'"

    service_name = data["Info"]["service_name"]

    try:
        service = client.services.get(service_name)
    except docker.errors.NotFound:
        return f"Error: Service '{service_name}' not found"
    except docker.errors.APIError as e:
        return f"Error: API error getting service '{service_name}': {e}"

    try:
        # Force update with no changes - this recreates tasks (restarts)
        service.update(force_update=True)
        return f"Service '{name}' restarted successfully"
    except docker.errors.APIError as e:
        return f"Error: Failed to restart service '{service_name}': {e}"


def admin_delete(name, username):
    """Stop and remove docker service
    Remove volumes and configs associated with the service

    This function attempts to remove all components even if some steps fail.
    It will continue attempting to clean up metadata, service, volume, and config
    even if one component fails.

    Args:
        name: Name of the service to remove
        username: Admin username performing the action

    Returns:
        String describing the results of the operation (success and failures)
    """
    result = f"Admin action requested: delete service: {name} "
    result += f"Requested by {username}\n\n"
    errors = []

    # Step 1: Get metadata from admin database
    state_info = admin_db.get_container_state(name)
    if state_info is None:
        return f"ERROR: Unable to find {name} in Admin DB. Cannot proceed without metadata.\n"

    data = admin_db.get_container_data(state_info.c_id)
    service_name = data["Info"].get("service_name", f"mydb_{name}")
    volume_name = data["Info"].get("volume_name", f"mydb_{name}")
    config_name = data["Info"].get("config_name", f"mydb_{name}_init.sql")

    result += f"Found container metadata (CID: {state_info.c_id})\n"
    result += f"  Service: {service_name}\n"
    result += f"  Volume:  {volume_name}\n"
    result += f"  Config:  {config_name}\n\n"

    # Step 2: Remove from admin database (do this early so it's marked as deleted)
    try:
        admin_db.delete_container_state(state_info.c_id)
        description = (
            f"Removed {name} by user {username} from admindb (CID: {state_info.c_id})"
        )
        admin_db.add_container_log(state_info.c_id, name, "deleted", description)
        result += "[OK] Metadata removed from admin database\n"
    except Exception as e:
        error_msg = f"[ERROR] Failed to remove metadata from admin database: {e}"
        result += error_msg + "\n"
        errors.append(error_msg)

    # Step 3: Remove Docker Swarm service (continue even if this fails)
    try:
        status = stop_remove(service_name)
        if "Error:" in status:
            result += f"[ERROR] Service removal: {status}\n"
            errors.append(f"Service removal failed: {status}")
        else:
            result += f"[OK] Service removed: {status}\n"
    except Exception as e:
        error_msg = f"Unexpected error removing service: {e}"
        result += f"[ERROR] {error_msg}\n"
        errors.append(error_msg)

    # Step 4: Remove Docker volume (continue even if this fails)
    try:
        status = volume_remove(volume_name)
        if "Issues removing" in status or "not found" in status.lower():
            result += f"[WARN] Volume removal: {status}\n"
            errors.append(f"Volume removal issue: {status}")
        else:
            result += f"[OK] Volume removed: {status}\n"
    except Exception as e:
        error_msg = f"Unexpected error removing volume: {e}"
        result += f"[ERROR] {error_msg}\n"
        errors.append(error_msg)

    # Step 5: Remove Docker config (continue even if this fails)
    try:
        status = docker_config_remove(config_name)
        if "Error" in status or "not found" in status.lower():
            result += f"[WARN] Config removal: {status}\n"
            # Don't treat "not found" as critical error
            if "not found" not in status.lower():
                errors.append(f"Config removal issue: {status}")
        else:
            result += f"[OK] Config removed: {status}\n"
    except Exception as e:
        error_msg = f"Unexpected error removing config: {e}"
        result += f"[ERROR] {error_msg}\n"
        errors.append(error_msg)

    # Summary
    result += f"\n{'=' * 60}\n"
    if errors:
        result += f"Deletion completed with {len(errors)} error(s)/warning(s)\n"
        result += "Errors:\n"
        for error in errors:
            result += f"  - {error}\n"
    else:
        result += "[OK] All components deleted successfully\n"

    # Send notification email
    subject = f"DBaaS: service {'partially' if errors else 'fully'} removed"
    send_mail(subject, result, mydb_config.supportEmail)

    return result


def remove_service(service_name):
    try:
        status = client.service.remove(service_name)
    except docker.errors.NotFound:
        return "Error"  # jfdey make better
    return True


def docker_config_remove(config_name):
    """remove the Docker Swarm config for a container"""
    try:
        config = client.configs.get(config_name)
        if config:
            config.remove()
            return f"Docker Config {config_name} removed."
    except NotFound:
        return "Docker config not found."
    except APIError as e:
        return f"Error occurered while removing {config_name}: {e}"


def display_services():
    """{"ID":"yv7ds8clfb86","Image":"postgres:17.4","Mode":"replicated","Name":"mydb_admin_db","Ports":"*:32009-\u003e5432/tcp","Replicas":"1/1"}"""
    fields = ["ID", "Name", "Mode", "Image", "Ports", "Status", "Up Time"]
    format_string = "{:<25} {:<25} {:<10} {:<20} {:<12} {:<8} {}"
    header = format_string.format(*fields)
    # Get services filtered by name
    services = client.services.list(filters={"name": "mydb"})

    # Convert to list of dictionaries
    body = ""
    for service in services:
        attrs = service.attrs
        target = attrs["Endpoint"]["Ports"][0]["TargetPort"]
        published = attrs["Endpoint"]["Ports"][0]["PublishedPort"]
        mapping = f"{target}:{published}"
        image = attrs["Spec"]["TaskTemplate"]["ContainerSpec"]["Image"].split("@")[0]
        tasks = service.tasks()
        status = tasks[0]["Status"]["State"]
        up_time = human_uptime(attrs["CreatedAt"])
        # Extract relevant information (docker service ls output)
        line = format_string.format(
            service.id,
            service.name,
            list(attrs["Spec"]["Mode"].keys())[0],  # 'Replicated' or 'Global'
            image,
            mapping,
            status,
            up_time,
        )
        body += line + "\n"
    return header, body
