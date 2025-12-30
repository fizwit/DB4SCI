import os
import subprocess
import time

import boto3
from botocore.exceptions import ClientError

from . import mydb_config


def create_backup_prefix(Name):
    """Create prefix for aws s3 backup
    Returns: (backup_id, prefix)
    backup_id format: YYYY-MM-DD_HH:MM:SS
    prefix format: /Name/YYYY-MM-DD_HH:MM:SS/
    V1 used '/prod/` for prefix
    V2 use '/mydb/` for prefix
    """
    t = time.localtime()
    backup_id = f"{t[0]}-{t[1]:02d}-{t[2]:02d}_{t[3]:02d}:{t[4]:02d}:{t[5]:02d}"
    prefix = f"/{mydb_config.s3_prefix_backup}/{Name}/{backup_id}/"

    return backup_id, prefix


def list_s3(Name):
    """return list of backup prefixes for a container.
    Note each prefix is PIT backup date, the backup files are
    in the PIT
    """
    cmd = f"aws s3 ls --recursive {mydb_config.AWS_BUCKET_NAME}/prod/{Name}"
    print(f"DEBUG: {__file__}.selecte list_s3 cmd: {cmd}")
    backups = os.popen(cmd).read().strip()
    return backups


def lastbackup_s3_prefix(Name):
    """Find the latest backup archive file for a container by searching AWS S3.

    AWS output format: "2025-01-15 14:30:00  12345678 prod/container_name/2025-01-15_14:30:00/*"
    return a single prefix for the latest backup
    """
    s3_cmd = f"aws s3 ls {mydb_config.AWS_BUCKET_NAME}/prod/{Name}/"
    command = s3_cmd.split()
    print(f"DEBUG aws_util.lastbackup_s3_prefix: cmd = {command}")

    # Execute the command
    try:
        p1 = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        p2 = subprocess.Popen(
            ["sort"],
            stdin=p1.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        p1.stdout.close()
        result = p2.communicate()
        if p2.returncode == 0:
            lines = result[0].strip().split("\n")
            if len(lines) > 0:
                return f"/prod/{Name}/{lines[-1].split()[1]}"
            else:
                return "Error: lastbackup_s3_prefix: No backups found"
        else:
            return "Error: lastbackup_s3_prefix: "
    except subprocess.CalledProcessError as e:
        return f"Error: with s3 cmd: {command}\n error occurred: {e}"


def list_s3_files(s3_url):
    """
    List all files in an S3 bucket with a given prefix.

    Args:
        s3_url (str): Full S3 URL including bucket and prefix
                     Example: "s3://bucket-name/prefix/path/"

    Returns:
        list: List of full S3 URLs for each file, or empty list if error

    Example:
        >>> files = list_s3_files("s3://my-bucket/backups/2025-01-15/")
        >>> print(files)
        ['s3://my-bucket/backups/2025-01-15/file1.sql',
         's3://my-bucket/backups/2025-01-15/file2.dump']
        response['Contents'] = {
            'Key': 'prod/gecco/gecco_2025-12-09_19-46/dump',
            'LastModified': datetime.datetime(2025, 12, 11, 2, 7, 16, tzinfo=tzutc()),
            'ETag': '"939837938b8c1dfc41bf5133849da7b4-9"',
            'ChecksumAlgorithm': ['CRC64NVME'],
            'ChecksumType': 'FULL_OBJECT',
            'Size': 73889487,
            'StorageClass': 'STANDARD'
        }
    """
    # Parse the S3 URL to extract bucket and prefix
    if not s3_url.startswith("s3://"):
        print(f"Error: Invalid S3 URL format: {s3_url}")
        return []

    # Remove 's3://' prefix
    path = s3_url[5:]

    # Split into bucket and prefix
    parts = path.split("/", 1)
    bucket_name = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""

    s3_client = boto3.client("s3")

    try:
        response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=prefix)

        file_list = []
        if "Contents" in response:
            for obj in response["Contents"]:
                # Reconstruct full S3 URL
                file_url = f"s3://{bucket_name}/{obj['Key']}"
                file_list.append(file_url)

        return file_list

    except ClientError as e:
        print(f"Error accessing S3 bucket: {e}")
        return []
    except Exception as e:
        print(f"Unexpected error: {e}")
        return []


def get_files_in_s3(prefix):
    s3_client = boto3.client("s3")

    boto_bucket = mydb_config.AWS_BUCKET_NAME.replace("s3://", "").rstrip("/")
    if prefix[0] == "/":
        prefix = prefix[1:]
    try:
        response = s3_client.list_objects_v2(Bucket=boto_bucket, Prefix=prefix)

        file_list = []
        if "Contents" in response:
            for obj in response["Contents"]:
                # Reconstruct full S3 URL
                file_url = f"{mydb_config.AWS_BUCKET_NAME}/{obj['Key']}"
                file_list.append(file_url)

        return file_list

    except ClientError as e:
        print(f"Error accessing S3 bucket: {e}")
        return []
    except Exception as e:
        print(f"Unexpected error: {e}")
        return []


def setup_parser():
    parser = argparse.ArgumentParser(
        description="aws_util modue test",
        usage="%(prog)s [options] module_name",
    )
    parser.add_argument(
        "--last_backup", type=str, required=False, help="get last S3 backup prefix"
    )
    parser.add_argument(
        "--get_files",
        type=str,
        required=False,
        help="return files in S3 bucker and prefix",
    )
    parser.add_argument(
        "--list_s3",
        type=str,
        required=False,
        help="list S3 prefixes for DB",
    )
    return parser.parse_args()


def main():
    args = setup_parser()
    bucket_name = os.getenv("AWS_BUCKET_NAME")
    boto_bucket = bucket_name.replace("s3://", "").rstrip("/")

    if args.last_backup:
        print(f"Testing lastbackup_s3_prefix, name: {args.last_backup}")
        prefix = lastbackup_s3_prefix(args.last_backup)
        print(f"Last backup found: {prefix}")
        files = get_files_in_s3(prefix)
        print(f"Files found: {files}")
    elif args.list_s3:
        print(f"Testing list_s3_prefixes, name: {args.list_s3}")
        prefixes = list_s3(bucket_name, args.list_s3)
        print(f"Prefixes found: {prefixes}")
    else:
        print("No action specified")


if __name__ == "__main__":
    import argparse

    main()
