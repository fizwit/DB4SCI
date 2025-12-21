# Release Notes

#### Version 2.0.1 Oct, Nov, Dec 2025

  - Major rewrite for Docker Swarm
  - Use Docker Swam API replace docker_util.py with swarm_util.py
  - docker stack deploy
  - Implement Migrate feature. Migrate databases from one platform to a new
    platform. Meta data is recovered from S3 backups and moved to the new
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
