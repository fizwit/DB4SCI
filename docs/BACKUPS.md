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

This ensures that database backups containing sensitive data are protected in cleartext form, both during transmission and while stored in S3.

### How Backups Work

Each database engine has a backup function that:

1. **Connects** to the database instance using admin credentials
2. **Dumps** the database using native tools (`pg_dump`, `mariadb-dump`, `mongodump`)
3. **Pipes** the output directly to `aws s3 cp -`
4. **Logs** backup metadata to the MyDB admin database

**Example PostgreSQL backup flow:**
```bash
pg_dump -F c dbname | aws s3 cp - s3://bucket/prefix/dbname.dump
```

### Backup Metadata

All backup operations are logged to the MyDB admin database in the `backups` table, tracking:
- Backup ID and timestamp
- S3 URL location
- Backup type [Daily, Weekly, User, Admin]
- Command executed
- Success/failure status

## Automated Nightly Backups

The `backup_util.backup_all()` function is responsible for performing nightly backups of all active database instances. The route `POST /cron/backup_all` triggers this function. The `TASK_TOKEN` is passed in the request header to verify the request is authorized. A cron job on the `dbaas` service triggers this route nightly. Each database instance has a `BACKUP_FREQ` metadata field that determines whether it should be backed up. 'Weekly' instances are backed up on Saturdays. 'Daily' instances are backed up every night. The option of 'None' means the instance should not be backed up.

### What the route `/cron/backup_all` Does

1. **Queries** the admin database for all active containers
2. **Checks** each container's `BACKUP_FREQ` metadata (Daily, Weekly, or None)
3. **Executes** database-specific backup commands that stream to S3
4. **Logs** results to the admin database
5. **Sends** email notification upon completion

### Backup Schedule

Backups are scheduled based on container metadata. I like to start backups at 1 AM every night so the backup process doesn't interfere with regular database usage and the start/stop times of the backups are in the same calendar day.

Users can trigger on-demand backups through the web interface at any time.

#### Installation Steps

Everything is done from the `dbaas` service. The **Dockerfile** sets up the cron job that triggers the backup process. `docker build .` uses the Dockerfile to build the image. The build process moves the file `etc/mydb_backup.crontab` into the container and sets up the cron job. If you want to change the backup schedule, you can modify the `etc/mydb_backup.crontab` file and rebuild the image.

** Test the backup route **

The `backup_all` route can be reached with the `/admin/backup_all` endpoint. You must be authenticated as an admin user to access this endpoint.

#### Monitoring Backups

The `Admin` GUI has an audit backup feature. This is implemented by writing a `start/end` backup
record to the `backup` log table. The audit makes sure there is a `start` and `end` message for each
database that requires backup.

The next best method to inspect backup files is to look at the **AWS S3** bucket for backup files,

**Email notifications:**

The `backup_all.py` script sends email notifications to addresses configured in `mydb_config.supportAdmin_email` upon completion or errors.

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
aws s3 cp s3://your-bucket/admin_db/mydb_admin_20250101.dump - | \
  pg_restore -h admin-db-host -U mydbadmin -d mydb_admin
```

**Note:** The admin database is also used for the **Migrate** feature when redeploying MyDB to a new environment or upgrading to a new version.

### Restoring Users Databases

Restore feature is available from the `Admin` menu. This will overwrite an
exiting database service. Restores are always complicated. It might be best
to create a new DB service to recover into.

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
      "Id": "DeleteOldBackups",
      "Status": "Enabled",
      "Prefix": "/mydb",
      "Expiration": {
        "Days": 90
      }
    },
    {
      "Id": "TransitionToGlacier",
      "Status": "Enabled",
      "Prefix": "",
      "Transitions": [
        {
          "Days": 30,
          "StorageClass": "GLACIER"
        }
      ]
    }
  ]
}
```

This configuration:
- Keeps recent backups in standard S3 storage for 30 days
- Moves backups to Glacier after 30 days
- Deletes backups after 90 days

## Restore Procedures

The `Admin` menu of the UI has a `Restore` feature. 

### Backup Script Fails to Connect to Database

All databases created by the service create a default `admin` account.
This `admin` account is used for backups. It's important that it is
not changed or removed. Use the `Connection URL` from the `Admin`
menu to generate a connection string. Test from a server with
the appropriate db tools (PostgreSQL, MariaDB, MongoDB) to
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

View the backup logs from the `Admin` menu.

## Support

For backup issues:
- Review backup table in admin database
- Verify AWS S3 access and credentials
- Contact your MyDB administrator
