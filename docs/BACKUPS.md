# MyDB Backup Guide

This guide explains MyDB's backup architecture and how to configure automated nightly backups.

## Backup Architecture

MyDB uses a **stream-to-S3** backup strategy where database dumps are piped directly to AWS S3 without creating intermediate local files. This approach:

- Minimizes local disk space requirements
- Provides immediate offsite storage
- Reduces backup window time
- Simplifies backup management, there is only one location for backups

### Security

All backups are stored in AWS S3 with **encryption at rest and in transit**:

- **Encryption in transit**: TLS/HTTPS is used for all data transfers to S3
- **Encryption at rest**: S3 server-side encryption (SSE-S3 or SSE-KMS) encrypts all stored backups
- **Access control**: IAM policies restrict access to authorized backup processes only

This ensures that database backups containing sensitive data are never exposed in cleartext, either during transmission or while stored in S3.

### How Backups Work

Each database engine has a backup function that:

1. **Connects** to the database instance using admin credentials
2. **Dumps** the database using native tools (`pg_dumpall` for Postgres globals, `pg_dump` per database, `mariadb-dump --all-databases`)
3. **Pipes** the output directly to `aws s3 cp -`
4. **Logs** a `start` and `end` record to the MyDB admin database

All dump tools run inside the `mydb_db4sci` container and connect over the network to the
published port of each database service.

**Example PostgreSQL backup flow:**
```bash
pg_dump -F c --dbname dbname | aws s3 cp - s3://bucket/mydb/<Name>/<backup_id>/<Name>_dbname.dump
```

Backups are written under `mydb/<Name>/<YYYY-MM-DD_HH:MM:SS>/` (`dev/` in the dev environment).

### Backup Metadata

All backup operations are logged to the MyDB admin database in the `backups` table, tracking:
- Backup ID and timestamp
- S3 URL location
- Backup type: `Admin` (nightly and Admin menu backups) or `User` (user-initiated)
- Command executed (passwords masked)
- Success/failure status: the `end` record's `err_msg` is empty on success and starts with
  `ERROR:` followed by the error on failure

## Automated Nightly Backups

The `backup_util.backup_all()` function performs nightly backups of all active database instances.
A cron job in the `mydb_db4sci` container runs it directly as a Python module (no web request is involved).
Each database instance has a `backup_freq` metadata field that determines whether it should be backed up.
'Weekly' instances are backed up on Saturdays. 'Daily' instances are backed up every night.
The option of 'None' means the instance should not be backed up.

### What `backup_all` Does

1. **Queries** the admin database for all active containers
2. **Checks** each container's `backup_freq` metadata (Daily, Weekly, or None)
3. **Executes** database-specific backup commands that stream to S3 (PostgreSQL and MariaDB)
4. **Logs** results to the admin database
5. **Continues** with the next container if one fails, so every backup is attempted
6. **Sends** a summary email when the loop completes; failures appear as `ERROR:` lines

### Backup Schedule

Backups are scheduled based on container metadata. I like to start backups at 1 AM every night so the backup process doesn't interfere with regular database usage and the start/stop times of the backups are in the same calendar day.

Users can trigger on-demand backups through the web interface at any time.

#### Installation Steps

Everything is done from the `mydb_db4sci` service. The **Dockerfile** sets up the cron job that runs the backup process. `docker build .` uses the Dockerfile to build the image. The build process installs `etc/mydb_backup.crontab` as the crontab for user `dbaas`:

```
5 1 * * * cd /app && /usr/local/bin/python3 -m mydb.backup_util --backup-all >> /var/log/backup_all.log 2>&1
```

Cron runs with a minimal environment. `entrypoint.sh` writes the container environment
(database URIs, AWS credentials) to `/etc/environment`, which cron loads for the job.
If you want to change the backup schedule, modify `etc/mydb_backup.crontab` and rebuild the image.

#### Test the Backup

Run the same command cron runs:

```bash
docker exec -u dbaas $(docker ps -q -f name=mydb_db4sci) \
  sh -c 'cd /app && /usr/local/bin/python3 -m mydb.backup_util --backup-all'
```

`backup_all` can also be started from the web UI at `/admin/backup_all`. You must be authenticated as an admin user to access this endpoint.

#### Monitoring Backups

The `Admin` GUI has an audit backup feature. Each backup writes a `start` and `end` record to the
`backups` log table. For each database that requires backup, the audit checks the most recent records
and reports one of:

- **Good**: the last backup started and finished within its policy window
- **Failed**: the last backup finished with an `ERROR:` message
- **Out of Policy**: the last backup is older than its Daily/Weekly window
- **Backup Running**: a backup has started and has not finished yet
- **Started but did not finish**: a `start` record with no matching `end`

The same report is available from the command line:

```bash
docker exec -u dbaas $(docker ps -q -f name=mydb_db4sci) \
  sh -c 'cd /app && /usr/local/bin/python3 -m mydb.backup_util [container_name]'
```

Output from the nightly cron job is in `/var/log/backup_all.log` in the `mydb_db4sci` container:

```bash
docker exec $(docker ps -q -f name=mydb_db4sci) tail -50 /var/log/backup_all.log
```

The next best method to inspect backup files is to look at the **AWS S3** bucket for backup files.

**Email notifications:**

`backup_util.backup_all()` sends a summary email to the addresses in `mydb_config.backup_admin_mail`
when the nightly run completes. A failed MariaDB backup also sends an error email to `mydb_config.supportEmail`.

## MyDB Admin Database Backups

The MyDB admin database itself is backed up every night by the backup_all script.  This database contains:

- Container metadata and state
- Backup history logs
- Action audit logs
- User activity records

### Restoring Admin Database

The admin database is critical for MyDB operations. To restore:

```bash
# Download from S3 and restore
aws s3 cp s3://your-bucket/mydb/<Name>/<backup_id>/<dump_file>.dump - | \
  pg_restore -h admin-db-host -U mydbadmin -d mydb_admin
```

**Note:** The admin database is also used for the **Migrate** feature when redeploying MyDB to a new environment or upgrading to a new version.

### Restoring Users Databases

Restore feature is available from the `Admin` menu. This will overwrite an
existing database service. Restores are always complicated. It might be best
to create a new DB service to recover into.

MariaDB restores skip the `mysql`, `information_schema`, `performance_schema` and `sys`
system databases in the dump; only user databases are restored.

## User-Initiated Backups

Users can trigger on-demand backups through the web interface:

1. Navigate to **Manage Services** → **Backup Database**
2. Select the database container
3. Backup executes immediately and streams to S3
4. Backup log entry created in admin database

User-initiated backups are marked with `backup_type='User'` in the backup logs.

## Backup Retention

### S3 Lifecycle Policies

Configure S3 bucket lifecycle rules to manage backup retention:

```json
{
  "Rules": [
    {
      "ID": "MyDBBackupRetention",
      "Status": "Enabled",
      "Filter": { "Prefix": "mydb/" },
      "Transitions": [
        {
          "Days": 30,
          "StorageClass": "GLACIER_IR"
        }
      ],
      "Expiration": {
        "Days": 120
      }
    }
  ]
}
```

This configuration:
- Keeps recent backups in standard S3 storage for 30 days
- Moves backups to Glacier Instant Retrieval after 30 days
- Deletes backups after 120 days

S3 prefixes do not start with `/`. Glacier Instant Retrieval can be read directly, so the
`Restore` feature still works on older backups. Its 90-day minimum storage charge is why
expiration is 120 days (30 + 90). With `GLACIER` (Flexible Retrieval), objects must be
restored from Glacier before `aws s3 cp` can read them.

## Restore Procedures

The `Admin` menu of the UI has a `Restore` feature.

### Backup Script Fails to Connect to Database

Backups connect with the service's admin account: `PG_ADMIN` (from the environment) for
PostgreSQL and `root` for MariaDB. It's important that these accounts are
not changed or removed. Use the `Connection CLI` from the `Admin`
menu to generate a connection string. Test from a server with
the appropriate db tools (PostgreSQL, MariaDB) to
run the connection string. This is the same connection method used
by backups. It should work.

##### Test with database client
```
psql -h db-host.example.org -p 32010 -U pgdba -d postgres
```

### AWS S3 Upload Fails

**Verify AWS credentials:**
```bash
aws sts get-caller-identity
aws s3 ls s3://your-bucket/
```

**Check IAM permissions:**
Required S3 permissions:
- `s3:PutObject`
- `s3:GetObject`
- `s3:ListBucket`

### Admin Database Connection Fails

**Check admin database is running:**
```bash
docker service ps mydb_admin_db
docker service logs mydb_admin_db
```

**Verify connection string:**
```bash
psql "$SQLALCHEMY_ADMIN_URI"
```

### Backup Logs Show Errors

View the backup logs from the `Admin` menu, or check `/var/log/backup_all.log` in the `mydb_db4sci` container.

## Support

For backup issues:
- Review backup table in admin database
- Verify AWS S3 access and credentials
- Contact your MyDB administrator
