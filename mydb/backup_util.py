"""
Backup, restore and audit helpers for MyDB.

Backups and restores stream through the AWS CLI with piped subprocesses:
    backup:  <backup_command> | [filter |] aws s3 cp - <s3_url>
    restore: aws s3 cp <s3_url> - | [filter |] <restore_command>

Audit MyDB backups by checking the MyDB Admin backup logs; verify that each
database in active state has been backed up within its policy.

import mydb.backup_util as backup_util
rpt = backup_util.backup_audit()
print(rpt[1])
"""

import datetime
import os
import shlex
import subprocess
import sys
import tempfile
import time

from mydb import mariadb_util, postgres_util
from . import admin_db, mydb_config
from .send_mail import send_mail

RESTORE_TIMEOUT = 3600  # One hour
BACKUP_FAILED = "ERROR"  # err_msg prefix on a failed backup's "end" log row


def backup_err_msg(success, msg="", context=None):
    """err_msg for an "end" backup_log row: empty on success, otherwise
    BACKUP_FAILED followed by <context> (e.g. a database name) and the error
    detail from an s3_piped_backup() message (the part after the command block).
    admin_db.backup_log keeps only the first 100 characters.
    """
    if success:
        return ""
    detail = msg.split("\n\n", 1)[-1].strip()
    if context:
        detail = f"{context}: {detail}"
    return f"{BACKUP_FAILED}: {detail}"


def hide_password(cmd, mask_password=None):
    """Hide passwords in a command string for logging"""
    safe_message = cmd
    if "mariadb-dump" in cmd or "mariadb" in cmd:
        safe_message = safe_message.replace(mydb_config.MARIADB_ROOT_PASSWORD, "xxxxx")
    if mask_password:
        safe_message = safe_message.replace(mask_password, "xxxxx")
    return safe_message


def _merge_env(env):
    """Merge custom environment variables with the current environment
    so PATH, AWS credentials and other critical variables are preserved.
    """
    if not env:
        return None
    process_env = os.environ.copy()
    process_env.update(env)
    return process_env


def _run_pipeline(commands, env=None, timeout=None):
    """Run commands as a shell-style pipeline: cmd1 | cmd2 | ... | cmdN

    Each stage's stderr goes to a temp file so a chatty stage can never
    block on a full pipe. Raises subprocess.TimeoutExpired after killing
    every stage if the pipeline does not finish within <timeout> seconds.

    Returns:
        tuple: ([(returncode, stderr), ...] one per stage, stdout of last stage)
    """
    procs = []
    err_files = []
    out_file = tempfile.TemporaryFile()
    upstream = None
    try:
        for i, argv in enumerate(commands):
            err_file = tempfile.TemporaryFile()
            err_files.append(err_file)
            last = i == len(commands) - 1
            proc = subprocess.Popen(
                argv,
                stdin=upstream,
                stdout=out_file if last else subprocess.PIPE,
                stderr=err_file,
                env=env,
            )
            # Close our copy so upstream gets SIGPIPE if downstream exits
            if upstream:
                upstream.close()
            upstream = proc.stdout
            procs.append(proc)

        deadline = time.monotonic() + timeout if timeout else None
        for proc in procs:
            remaining = None if deadline is None else max(0, deadline - time.monotonic())
            proc.wait(timeout=remaining)
    except BaseException:
        for proc in procs:
            proc.kill()
            proc.wait()
        for f in err_files + [out_file]:
            f.close()
        raise

    def read(f):
        f.seek(0)
        data = f.read().decode(errors="replace")
        f.close()
        return data

    results = [(proc.returncode, read(f)) for proc, f in zip(procs, err_files)]
    return (results, read(out_file))


def _pipeline_error(names, results):
    """Return an error message for the first failed stage, or None"""
    for name, (returncode, stderr) in zip(names, results):
        if returncode != 0:
            return f"{name} failed (exit code {returncode}):\n{stderr}\n"
    return None


def s3_piped_backup(
    backup_command, s3_url, env=None, filter=None, timeout=None, mask_password=None
):
    """Execute database backup using piped subprocess commands to S3

    Pipes: <backup_command> | [filter |] aws s3 cp - <s3_url>

    Args:
        backup_command (str): Database backup command that writes to stdout
        s3_url (str): Full S3 URL where backup will be stored
        env (dict): Optional environment variables to add/override (merged with os.environ)
                    For PostgreSQL, pass {"PGPASSWORD": "password"}
        filter (str): Optional shell-quoted command piped between the backup
                      command and the S3 upload (e.g. "gzip -c")
        timeout (int): Optional timeout in seconds (default: no timeout)
        mask_password (str): Optional password to hide in log messages

    Returns:
        tuple: (success: bool, message: str)

    Example:
        backup_cmd = "pg_dump -h host -U user dbname"
        env = {"PGPASSWORD": "password"}
        success, msg = s3_piped_backup(backup_cmd, s3_url, env=env)
    """
    aws_cmd = ["aws", "--only-show-errors", "s3", "cp", "-", s3_url]
    commands = [shlex.split(backup_command)]
    names = ["Backup command"]
    if filter:
        commands.append(shlex.split(filter))
        names.append("Filter command")
    commands.append(aws_cmd)
    names.append("S3 upload")

    stages = [backup_command] + ([filter] if filter else []) + [" ".join(aws_cmd)]
    safe_message = hide_password(" | \\\n  ".join(stages), mask_password)
    print(f"DEBUG backup_util.s3_piped_backup: {safe_message}")

    result_msg = f"Backing up to S3: {s3_url}\n"
    result_msg += f"Command: {safe_message}\n\n"

    try:
        results, stdout = _run_pipeline(commands, env=_merge_env(env), timeout=timeout)
    except subprocess.TimeoutExpired:
        return (False, result_msg + f"Backup timed out after {timeout} seconds.\nBackup incomplete\n")
    except Exception as e:
        return (False, result_msg + f"Unexpected error during backup: {e}\n")

    error_msg = _pipeline_error(names, results)
    # aws runs with --only-show-errors, so any stderr from it is an error
    aws_stderr = results[-1][1]
    if not error_msg and aws_stderr:
        error_msg = f"S3 upload failed:\n{aws_stderr}\n"
    if error_msg:
        error_msg = hide_password(error_msg, mask_password)
        print(f"ERROR: {error_msg}")
        result_msg += error_msg
        if stdout:
            result_msg += f"stdout: {stdout}\n"
        return (False, result_msg)

    result_msg += "Backup completed successfully\n"
    if stdout:
        result_msg += f"stdout: {stdout}\n"
    return (True, result_msg)


def s3_file_restore(s3_url, restore_command, env=None, timeout=RESTORE_TIMEOUT):
    """Restore database from an S3 file that must be local (pg_restore)

    1. Downloads the S3 file to /tmp
    2. Runs <restore_command> <local_file>
    3. Removes the local file

    Args:
        s3_url (str): Full S3 URL to backup file
        restore_command (str): e.g. "pg_restore -h host -p port -d dbname -U user"
        env (dict): Environment variables (e.g., {"PGPASSWORD": "password"})
        timeout (int): Timeout in seconds (default 3600)

    Returns:
        tuple: (success: bool, message: str)
    """
    from . import aws_util

    success, local_file = aws_util.save_s3_obj(s3_url, os.path.basename(s3_url))
    if not success:
        return (False, f"Failed to download S3 file: {local_file}")

    restore_cmd_list = restore_command.split() + [local_file]
    safe_message = hide_password(" ".join(restore_cmd_list))
    print(f"DEBUG backup_util.s3_file_restore: {safe_message}")

    result_msg = f"Restoring from local file: {local_file}\n"
    result_msg += f"Command: {safe_message}\n\n"

    try:
        results, stdout = _run_pipeline(
            [restore_cmd_list], env=_merge_env(env), timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return (False, f"Restore timed out after {timeout} seconds.\nRestore incomplete")
    except Exception as e:
        return (False, f"Unexpected error during restore: {e}")
    finally:
        try:
            os.remove(local_file)
        except FileNotFoundError:
            print(f"backup_util.s3_file_restore: Failed to remove {local_file}: File not found")

    returncode, stderr = results[0]
    if returncode != 0:
        error_msg = f"Restore command failed (exit code {returncode}):\n{stderr}\n"
        print(f"ERROR: {error_msg}")
        result_msg += error_msg
        if stdout:
            result_msg += f"stdout: {stdout}\n"
        return (False, result_msg)

    result_msg += "Restore completed successfully\n"
    if stdout:
        result_msg += f"stdout: {stdout}\n"
    if stderr:
        result_msg += f"warnings: {stderr}\n"
    return (True, result_msg)


def s3_piped_restore(
    s3_url,
    restore_command,
    env=None,
    filter=None,
    timeout=RESTORE_TIMEOUT,
    mask_password=None,
):
    """Execute S3 restore using piped subprocess commands

    Pipes: aws s3 cp <s3_url> - | [filter |] <restore_command>

    Args:
        s3_url (str): Full S3 URL to backup file
        restore_command (str): Database restore command that reads from stdin
        env (dict): Optional environment variables to add/override (merged with os.environ)
        filter (str): Optional shell-quoted command piped between the S3 copy
                      and the restore command (e.g. an awk script)
        timeout (int): Timeout in seconds (default 3600)
        mask_password (str): Optional password to hide in log messages

    Returns:
        tuple: (success: bool, message: str)

    Example:
        s3_url = "s3://bucket/path/to/backup.dump"
        restore_cmd = "pg_restore -h host -p port -d dbname -U user"
        password_env = {"PGPASSWORD": "mypassword"}
        success, msg = s3_piped_restore(s3_url, restore_cmd, env=password_env)
    """
    aws_cmd = ["aws", "s3", "cp", s3_url, "-"]
    commands = [aws_cmd]
    names = ["S3 download"]
    if filter:
        commands.append(shlex.split(filter))
        names.append("Filter command")
    commands.append(restore_command.split())
    names.append("Restore command")

    stages = [" ".join(aws_cmd)] + ([filter] if filter else []) + [restore_command]
    safe_message = hide_password(" | \\\n  ".join(stages), mask_password)
    print(f"DEBUG backup_util.s3_piped_restore: {safe_message}")

    result_msg = f"Restoring from S3: {s3_url}\n"
    result_msg += f"Command: {safe_message}\n\n"

    try:
        results, stdout = _run_pipeline(commands, env=_merge_env(env), timeout=timeout)
    except subprocess.TimeoutExpired:
        return (False, f"Restore timed out after {timeout} seconds.\nRestore incomplete")
    except Exception as e:
        return (False, f"Unexpected error during restore: {e}")

    # Report the restore command's failure first; an upstream SIGPIPE is a symptom
    error_msg = _pipeline_error(names[::-1], results[::-1])
    if error_msg:
        error_msg = hide_password(error_msg, mask_password)
        print(f"ERROR: {error_msg}")
        result_msg += error_msg
        if stdout:
            result_msg += f"stdout: {stdout}\n"
        return (False, result_msg)

    result_msg += f"stdout: {stdout}\n"
    stderr = results[-1][1]
    if stderr:
        result_msg += f"warnings: {hide_password(stderr, mask_password)}\n"
    result_msg += "Restore completed successfully\n"
    return (True, result_msg)


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
    days = 7 if info["backup_freq"] == "Weekly" else 1
    now = datetime.datetime.now()
    since = now - datetime.timedelta(days=days)

    start_ts = end_ts = None
    start_id = end_id = None
    end_err = ""
    for row in admin_db.backup_lastlog(c_id):
        if row.state == "start":
            start_ts = row.ts
            start_id = row.backup_id
        elif row.state == "end":
            end_ts = row.ts
            end_id = row.backup_id
            end_err = row.err_msg or ""

    if start_ts is None:
        return msg + "No backups found\n"
    out_of_policy = start_ts < since

    if start_id and end_id:
        if start_id == end_id:
            if end_err.startswith(BACKUP_FAILED):
                msg += "%s Failed: %s\n" % (start_ts, " ".join(end_err.split()))
            elif out_of_policy:
                msg += "%s Out of Policy (%s)\n" % (start_ts, now - start_ts)
            else:
                msg += "%s Good   (%s)\n" % (start_ts, end_ts - start_ts)
        else:
            msg += "%s Backup Running\n" % start_ts
    elif out_of_policy:
        msg += "%s Out of Policy; Started but did not finish!\n" % start_ts
    else:
        msg += "%s Started but did not finish!\n" % start_ts
    return msg


def backup_report(c_id, name):
    """Backup log history for one container, by <c_id> or <name>"""
    if c_id is None:
        state_info = admin_db.get_container_state(name)
        if not state_info:
            return ("Backup report", f"Container not found: {name}\n")
        c_id = state_info.c_id
    data = admin_db.get_container_data(c_id)
    if not data:
        return ("Backup report", f"Container not found: cid={c_id}\n")
    header = "Backup report for {}".format(data["Info"]["Name"])
    msg = get_backup_log(data["Info"], c_id)
    return (header, msg)


def backup_audit_all():
    """inspect the backup logs for every container that is running.
    get list of all "running" containers
    inspect backup logs based on backup policy for each container
    """
    header = "%-30s %-10s %-6s %-26s Status (Duration)" % (
        "Container",
        "DB Type",
        "Policy",
        "Start Time (UTC)",
    )
    msg = ""
    for c_id, con_name in admin_db.list_active_containers():
        data = admin_db.get_container_data(c_id)
        policy = data["Info"].get("backup_freq")
        if policy is None:
            msg += "Extreme Badness: Backup policy not set for %s.\n" % con_name
        elif policy in ("Daily", "Weekly"):
            msg += check_backup_logs(data["Info"], c_id)
    return (header, msg)


def backup_audit(name=None, c_id=None):
    if name:
        return backup_report(None, name)
    if c_id:
        return backup_report(c_id, None)
    return backup_audit_all()


def backup_all():
    """backup all running containers
    get list of all "running" containers
    Check <backup_freq> for each container
    backup_freq can have the following values: ['None', 'Daily', 'Weekly']
    """
    saturday = 5
    start = time.strftime("%A, %B %d, %Y %H:%M:%S")
    msg = "Backup_all has completed database backups for all containers.\n"
    msg += f"Environment: {mydb_config.DB4SCI_ENV}\n"
    msg += f"Start: {start}\n"
    print(msg)
    for c_id, con_name in admin_db.list_active_containers():
        info = admin_db.get_container_data(c_id)["Info"]
        policy = info.get("backup_freq")
        print(f"backup_all: container: {con_name} backup_freq: {policy}")
        if policy is None or policy == "None":
            continue
        if policy == "Weekly" and time.localtime().tm_wday != saturday:
            continue
        info["username"] = "cron"
        dbengine = info.get("dbengine")
        print(f"backup_all: dbengine: {dbengine}")
        if dbengine == "Postgres":
            msg += postgres_util.pg_backup(info, "Admin", c_id)
        elif dbengine == "MariaDB":
            msg += mariadb_util.backup(c_id, info, "Admin")
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
