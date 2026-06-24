import datetime
import os
import subprocess
import sys
import time

from mydb import postgres_util
from . import admin_db, mydb_config
from .send_mail import send_mail


"""
audit MyDB backups. Check MyDB Admin backup logs.
verify that each data base in active state has been
backed up.

import mydb.backup_util as backup_util
rpt = backup_util.backup_audit()
print(rpt[1])

"""


def hide_password(cmd_list):
    """Hide password in command list"""
    if "mongodump" in cmd_list or "mongorestore" in cmd_list:
        safe_message = cmd_list.replace(
            mydb_config.accounts["MongoDB"]["admin_pass"], "xxxxx"
        )
    if "mariadb-dump" in cmd_list or "mariadb" in cmd_list:
        safe_message = cmd_list.replace(
            mydb_config.accounts["MariaDB"]["admin_pass"], "xxxxx"
        )
    else:
        safe_message = cmd_list
    return safe_message


def s3_piped_backup(backup_command, s3_url, env=None):
    """Execute database backup using piped subprocess commands to S3

    Uses subprocess.Popen to pipe: <backup_command> | aws s3 cp - <s3_url>

    Args:
        backup_command (str): Database backup command that writes to stdout
        s3_url (str): Full S3 URL where backup will be stored
        env (dict): Optional environment variables to add/override (merged with os.environ)
                   For PostgreSQL, pass {"PGPASSWORD": "password"}

    Returns:
        tuple: (success: bool, message: str)

    Example:
        # PostgreSQL - password in environment
        backup_cmd = "pg_dump -h host -U  user  dbname"
        env = {"PGPASSWORD": "password"}
        success, msg = s3_piped_backup(backup_cmd, s3_url, env=env)
    """
    import shlex

    backup_cmd_list = shlex.split(backup_command)

    # Merge custom environment variables with current environment
    # This ensures PATH and other critical variables are preserved
    if env:
        process_env = os.environ.copy()
        process_env.update(env)
    else:
        process_env = None

    # Build AWS S3 copy command
    aws_cmd = ["aws", "--only-show-errors", "s3", "cp", "-", s3_url]

    # Create command for logging
    aws_cmd_str = " ".join(aws_cmd)
    full_command = f"{backup_cmd_list} | \\\n  {aws_cmd_str}"
    safe_message = hide_password(backup_command)

    print(f"DEBUG backup_util.s3_piped_backup: {full_command} env: {env}")

    result_msg = f"Backing up to S3: {s3_url}\n"
    result_msg += f"Command: {safe_message}\n\n"

    try:
        # Start backup process
        p1 = subprocess.Popen(
            backup_cmd_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=process_env,
        )

        # Start S3 upload process, reading from backup stdout
        p2 = subprocess.Popen(
            aws_cmd,
            stdin=p1.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Close p1 stdout to allow p1 to receive SIGPIPE if p2 exits
        p1.stdout.close()

        # Wait for both processes to complete
        # p2.communicate() waits for p2 to finish
        stdout, stderr = p2.communicate()

        # Wait for p1 to finish and get its return code
        p1.wait()

    except Exception as e:
        error_msg = f"Unexpected error during backup: {e}"
        return (False, error_msg)

    # Check results after processes complete
    # Check p1 (backup command) return code first
    if p1.returncode != 0:
        # Read p1 stderr if available
        p1_stderr = p1.stderr.read() if p1.stderr else ""
        error_msg = f"Backup command failed (exit code {p1.returncode}):\n{p1_stderr}\n"
        print(f"ERROR: {error_msg}")
        result_msg += error_msg
        return (False, result_msg)

    # Check p2 (S3 upload) return code
    if p2.returncode != 0 or (stderr and len(stderr) > 0):
        error_msg = f"S3 upload failed (exit code {p2.returncode}):\n{stderr}\n"
        print(f"ERROR: {error_msg}")
        result_msg += error_msg
        if stdout:
            result_msg += f"stdout: {stdout}\n"
        return (False, result_msg)

    # Success
    result_msg += "Backup completed successfully\n"
    if stdout:
        result_msg += f"stdout: {stdout}\n"
    return (True, result_msg)


def s3_file_restore(s3_url, restore_command, env):
    """Restore database from S3 file using pg_restore

    pg_restore requires a local file, so this function:
    1. Downloads the S3 file to /tmp
    2. Runs pg_restore on the local file

    Args:
        s3_url (str): Full S3 URL to backup file
        restore_command (str): Database restore command (e.g., "pg_restore -h host -p port -d dbname -U user")
        env (dict): Environment variables (e.g., {"PGPASSWORD": "password"})

    Returns:
        tuple: (success: bool, message: str)
    """
    from . import aws_util

    # Extract filename from S3 URL
    fname = os.path.basename(s3_url)

    # Download S3 file to /tmp
    success, result = aws_util.save_s3_obj(s3_url, fname)
    if not success:
        return (False, f"Failed to download S3 file: {result}")

    local_file = result  # Path to downloaded file in /tmp

    # Build restore command with local file
    restore_cmd_list = restore_command.split()
    restore_cmd_list.append(local_file)

    # Merge custom environment variables with current environment
    if env:
        process_env = os.environ.copy()
        process_env.update(env)
    else:
        process_env = None

    # Create safe command for logging
    safe_message = hide_password(" ".join(restore_cmd_list))

    print(f"DEBUG backup_util.s3_file_restore: {restore_cmd_list}")
    print(f"DEBUG backup_util.s3_file_restore: local_file: {local_file}")

    result_msg = f"Restoring from local file: {local_file}\n"
    result_msg += f"Command: {safe_message}\n\n"

    timeout = 3600  # One hour timeout

    try:
        # Run restore command
        p1 = subprocess.Popen(
            restore_cmd_list,
            env=process_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Wait for completion with timeout
        stdout, stderr = p1.communicate(timeout=timeout)

        # Check return code
        if p1.returncode != 0:
            error_msg = f"Restore command failed (exit code {p1.returncode}):\n{stderr}\n"
            print(f"ERROR: {error_msg}")
            result_msg += error_msg
            if stdout:
                result_msg += f"stdout: {stdout}\n"
            # Clean up downloaded file
            try:
                os.remove(local_file)
            except FileNotFoundError:
                print(f"backup_util.s3_file_restore: Failed to remove {local_file}: File not found")
            return (False, result_msg)
        else:
            result_msg += "Restore completed successfully\n"
            if stdout:
                result_msg += f"stdout: {stdout}\n"
            if stderr:
                result_msg += f"warnings: {stderr}\n"
            # Clean up downloaded file
            try:
                os.remove(local_file)
            except FileNotFoundError:
                print(f"backup_util.s3_file_restore: Failed to remove {local_file}: File not found")
            return (True, result_msg)

    except subprocess.TimeoutExpired:
        # Kill process if timeout
        p1.kill()
        error_msg = f"Restore timed out after {timeout} seconds.\nRestore incomplete"
        # Clean up downloaded file
        try:
            os.remove(local_file)
        except FileNotFoundError:
            print(f"backup_util.s3_file_restore: Failed to remove {local_file}: File not found")
        return (False, error_msg)
    except Exception as e:
        error_msg = f"Unexpected error during restore: {e}"
        # Clean up downloaded file
        try:
            os.remove(local_file)
        except:
            pass
        return (False, error_msg)


def s3_piped_restore(s3_url, restore_command, env=None):
    """Execute S3 restore using piped subprocess commands with Popen

    Uses subprocess.Popen to pipe: aws s3 cp <s3_url> - | <restore_command>

    Args:
        s3_url (str): Full S3 URL to backup file
        restore_command (str or list): Database restore command that reads from stdin
                                       Can be a string or list of command arguments
        env (dict): Optional environment variables to add/override (merged with os.environ)
        timeout (int): Timeout in seconds (default 1200 = 20 minutes)

    Returns:
        tuple: (success: bool, message: str)

    Example:
        s3_url = "s3://bucket/path/to/backup.dump"
        restore_cmd = "pg_restore -h host -p port -d dbname -U user"
        password_env = {"PGPASSWORD": "mypassword"}
        success, msg = s3_piped_restore(s3_url, restore_cmd, env=password_env)
    """
    timeout = 3600  # One hour timeout
    # Build AWS S3 copy command
    aws_cmd = ["aws", "s3", "cp", s3_url, "-"]
    restore_cmd_list = restore_command.split()

    # Merge custom environment variables with current environment
    # This ensures PATH and other critical variables are preserved
    if env:
        process_env = os.environ.copy()
        process_env.update(env)
    else:
        process_env = None

    # Create safe command for logging
    aws_cmd_str = " ".join(aws_cmd)
    full_command = f"{aws_cmd_str} |  \\\n {restore_command}"
    safe_message = hide_password(full_command)

    print(f"DEBUG backup_util.s3_piped_restore: {full_command}")
    print(f"command array: {restore_cmd_list}")
    result_msg = f"Restoring from S3: {s3_url}\n"
    result_msg += f"Command: {safe_message}\n\n"

    try:
        # Start AWS S3 copy process
        p1 = subprocess.Popen(aws_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Start restore process, reading from S3 copy stdout
        p2 = subprocess.Popen(
            restore_cmd_list,
            env=process_env,
            stdin=p1.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Close p1 stdout to allow p1 to receive SIGPIPE if p2 exits
        p1.stdout.close()

        # Wait for completion with timeout
        stdout, stderr = p2.communicate(timeout=timeout)

        # Check return codes
        if p2.returncode != 0:
            error_msg = f"Error during restore (exit code {p2.returncode}):\n{stderr}\n"
            print(f"ERROR: {error_msg}")
            result_msg += error_msg
            if stdout:
                result_msg += f"stdout: {stdout}\n"
            return (False, result_msg)
        else:
            result_msg += f"stdout: {stdout}\n"
            if stderr:
                result_msg += f"warnings: {stderr}\n"
            result_msg += "Restore completed successfully\n"
            return (True, result_msg)

    except subprocess.TimeoutExpired:
        # Kill processes if timeout
        p1.kill()
        p2.kill()
        error_msg = f"Restore timed out after {timeout} seconds.\nRestore incomplete"
        return (False, error_msg)
    except Exception as e:
        error_msg = f"Unexpected error during restore: {e}"
        return (False, error_msg)


def get_backup_log(info, c_id):
    """query backup log for history of error messages"""
    result = admin_db.backup_taillog(c_id, tail=10)
    start_format = "{:5} {}  command: {}\n"
    end_format = "{:5} {}  Error: {}\n\n"
    msg = ""
    for row in result:
        if row.state == "start":
            msg += start_format.format(row.state, row.ts, row.command)
        else:
            msg += end_format.format(row.state, row.ts, row.err_msg)
    return msg


def check_backup_logs(info, c_id):
    """query backup logs
    verify that backup started and ended
    verify that backup was run within policy (Daily or Weekly)
    """
    msg = "%-30s %-10s %-6s " % (info["Name"], info["dbengine"], info["backup_freq"])
    policy = info["backup_freq"]
    now = datetime.datetime.now()
    if policy == "Daily":
        since = now - datetime.timedelta(days=1)
    elif policy == "Weekly":
        since = now - datetime.timedelta(days=7)
    result = admin_db.backup_lastlog(c_id)
    start_ts = 0
    start_id = end_id = None
    out_of_policy = False
    for row in result:
        if row.state == "start":
            start_ts = row.ts
            start_id = row.backup_id
            if row.ts < since:
                out_of_policy = True
        if row.state == "end":
            end_ts = row.ts
            end_id = row.backup_id
            url = row.url
    if start_id and end_id:
        if start_id == end_id:  # this is good
            duration = end_ts - start_ts
            if out_of_policy:
                msg += "%s Out of Policy (%s)\n" % (start_ts, now - start_ts)
            else:
                msg += "%s Good   (%s)\n" % (start_ts, duration)
        else:
            msg += "%s Backup Running\n" % start_ts
    else:
        if out_of_policy:
            msg += "%s Out of Policy; Started but did not finish!\n" % start_ts
        else:
            msg += "%s Started but did not finish!\n" % start_ts
    return msg

    header = "Backup Logs for {}\n\n".format(data["Info"]["Name"])
    header += "%-26s Error Msg" % ("Start Time (UTC)")
    if "backup_freq" in data["Info"]:
        policy = data["Info"]["backup_freq"]
    else:
        msg += "Extreme Badness: Backup policy not set for %s.\n" % name
        return (header, msg)
    if policy == "Daily" or policy == "Weekly":
        status = check_backup_logs(data["Info"], c_id)
        msg += status


def backup_report(c_id, name):
    data = admin_db.get_container_data(name, c_id)
    header = "Backup report for {}".format(data["Info"]["Name"])
    if c_id is None:
        state_info = admin_db.get_container_state(name)
        c_id = state_info.c_id
    msg = get_backup_log(data["Info"], c_id)
    return (header, msg)


def backup_audit_all():
    """inspect the backup logs for every container that is running.
    get list of all "running" containers
    inspect backup logs based on backup policy for each container
    """
    check_list = []
    containers = admin_db.list_active_containers()
    header = "%-30s %-10s %-6s %-26s Status (Duration)" % (
        "Container",
        "DB Type",
        "Policy",
        "Start Time (UTC)",
    )
    msg = ""
    for c_id, con_name in containers:
        data = admin_db.get_container_data("", c_id)
        if "backup_freq" in data["Info"]:
            policy = data["Info"]["backup_freq "]
        else:
            msg += "Extreme Badness: Backup policy not set for %s.\n" % con_name
            continue
        if policy == "Daily" or policy == "Weekly":
            status = check_backup_logs(data["Info"], c_id)
            msg += status
    return (header, msg)


def backup_audit(name=None, c_id=None):
    if name:
        (header, body) = backup_report(None, name)
    if c_id:
        (header, body) = backup_report(c_id, None)
    else:
        (header, body) = backup_audit_all()
    return (header, body)

def backup_all():
    """backup all running containers
    get list of all "running" containers
    Check <backup_freq> for each container
    backup_freq can have the following values: ['None', 'Daily', 'Weekly']
    """
    t = time.localtime()
    DofW = t.tm_wday
    s = time.strftime("%A, %B %d, %Y %H:%M:%S")
    msg = "Backup_all has completed database backups for all containers.\n"
    msg += f"Environment: {mydb_config.DB4SCI_ENV}\n"
    msg += f"Start: {s}\n"
    print(msg)
    containers = admin_db.list_active_containers()
    for c_id, con_name in containers:
        data = admin_db.get_container_data("", c_id)
        info = data["Info"]
        print(f"Back_all: contaner: {con_name} {info}")
        # backup_freq': 'Daily',
        if "backup_freq" in info:
            policy = info["backup_freq"]
            print(f"backup_freq: policy: {policy}")
        else:
            continue
        if policy == "Weekly" and DofW != 5: # Saturday
            continue
        elif policy == "None":
            continue
        info['username'] = 'cron'
        if 'dbengine' in info:
            print(f"Back_all: dbengine: {info['dbengine']}")
            if info['dbengine'] == 'Postgres':
                message = postgres_util.backup(c_id, info, 'Admin')
                msg += message
    msg += f"End: {time.strftime('%A, %B %d, %Y %H:%M:%S')}\n"
    send_mail("MyDB: backup_all db", msg, mydb_config.backup_admin_mail)
    return msg


if __name__ == "__main__":
    if len(sys.argv) > 1:
        (header, body) = backup_audit(name=sys.argv[1])
    else:
        (header, body) = backup_audit()
    print(header)
    print(body)
