# Release Notes

#### Version 2.0.4.3 Jul 1, 2026

 - refactor admin_db.get_container_state(container_name)
 - refactor admin_db.get_container_data(container_id)

 - Improve readablity of Config
organizationName -> institutionName= "Your Univercity"
supportOrganization -> supportOrgName = "support group at Univercity"
supportEmail -> supportOrgEmail = "HPC-help@univercity.edu"

supportPerson ->  supportStaff = ["Jane Doe", "Fred"]
supportAdmin  ->  supportEmail = ["jdoe@univercity.edu"]
backup_admin_mail = ["backup_admin@univercity.edu"]

#### Version 2.0.4  Jun 2026
  - Remove the backup_all.sh script. Create endpoint for backup_all, and rewrite as Python3
    add backup_util:backup_all function.
    add route `cron/backup_all`  mydb_views. The route is protected by checking for the http
    header : DB4SCI_Task_Token
    add route `admin/backup_all`  This route is protected by users with Admin privilages as defined
    in mydb/mydb_config.py

  - Change DBAAS_ENV to DB4SCI_ENV Lets be more consistent with Environment names
  - add `extract_dbname` too postgres_util.py;utility function to extract the database name from an S3 backup object. Postgres database can have multiple databases in a single backup. The dbname is needed for the connection string.

### Version 2.0.3  May 2026 (not released)
  - remove docker secrets
  - Fix major Postgres Restore issues and recover issues

#### Version 2.0.1 Oct, Nov, Dec 2025

  - Major rewrite for Docker Swarm
  - Use Docker Swam API replace docker_util.py with swarm_util.py
  - docker stack deploy
  - Implement Migrate feature. Migrate databases from one platform to a new
    platform. Meta data is restored from S3 backups and moved to the new
    platform. 
  - Update container deployments using entry points to create user accounts.
  - Switch to Docker volumes, configs, and secrets.
  - Update older Python 2.7 code
  - New admin features for inspecting meta data and session info
  - feature to create e-mail list of users and containers
  - feature create command line connection string for database connection.
  - `touched` meta field. Users need to `touch` there containers to keep them from
    being de-commisioned. 
  - For local development: Install all required Python deps with venv -> .mydb_venv
  - remove passwords from docker images, remove passwords from logs, remove passwords from
    display messages and error messages. `safe_message` variable 

#### TODO
  - Database restore is based on what is currently in the "active" state. It could be possible to
    restore data for a container that was removed, before the backup purge cycle.

#### Postgres update Version 2.0.1
  - POSTGRES_DB is defined by the user, POSTRES_USER, POSTGRES_PASSWORD are defined by the application
    for backup purposes. User accounts are created at startup with the initdb scripts.
  - Create user level accounts with a startup script.
  - Set shared buffers at startup  
  - Pipe backups to AWS S3, do not use local storage


 
#### version 1.8.4.0 Dec, 2023

 - login_required decorator 

#### Version 1.8.1.0 July 26, 2022

- new improved menu, Pull down for "Manage Containers" which will make
  the menu more extensible.
  [List, Restart, Delete, List S3, Backup, Migrate] 
- Add feature to clone PostgreSQL container. New name is created, but
  all meta data is retained. Can add additional options like 'shm_size'.


Version 1.7.1.0 October 5, 2020
----------------------------

bug and feature release

- admin_db.py add du_bytes function to display size of DBVOL. DBVOL is the data volume of a
  database container. du_bytes can be used for an esitmate of the backup size.
- admin/du report size in human readable and in bytes
- admin/backup_audit - add optional argment to speicify container. If c_id or name argument is
  used the last 10 backup logs are displayed for the container.
- AWS s3 backups - If estimated backup is larger than 50GB add the  --expected-size= argument
  to the aws s3 cp command. This fixes backup issue for MariaDB databases that are over 50Gb in
  size. This error is specific to Redcap
- mydb/backup_util.py Add feature to display reports from the command line. If argument is container name, errors are
  displayed for the single container. Huge help with debugging backups.
