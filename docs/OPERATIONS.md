# MyDB Operations Guide

This document provides troubleshooting and operational procedures for the MyDB Database-as-a-Service platform.

## Table of Contents
- [Web UI Admin Features](#web-ui-admin-features)
- [Service Health Checks](#service-health-checks)
- [Docker Service Management](#docker-service-management)
- [Log Investigation](#log-investigation)
- [External Connectivity Testing](#external-connectivity-testing)
- [Direct Database Access](#direct-database-access)
- [Storage and Volume Management](#storage-and-volume-management)
- [Admin Database Queries](#admin-database-queries)

---

## Web UI Admin Features

The MyDB web interface provides powerful admin features for troubleshooting and managing user database containers. Access these features from the **Admin** dropdown menu (requires admin privileges).

### Connection CLI

The **Connection CLI** feature provides ready-to-use connection strings for connecting directly to user database containers.

**How to use:**

1. Navigate to **Admin** → **Connection CLI**
2. Select the user's database container
3. Copy the provided connection command
4. Load the appropriate database module (if using environment modules)
5. Paste and execute the connection command

**Example workflow for PostgreSQL:**

```bash
# 1. Load PostgreSQL module
module load PostgreSQL

# 2. Paste the Connection CLI command (copied from web UI)
psql -h mydb.yourorg.edu -p 32015 -d mydb_name -U mydbadmin

# You're now connected to the database with admin privileges
```

**Example workflow for MariaDB:**

```bash
# 1. Load MariaDB module (if available)
module load MariaDB

# 2. Paste the Connection CLI command
mariadb -h mydb.yourorg.edu -P 32016 -u mydbadmin -p

# Enter password when prompted
```

**Why this is useful:**

- **Quick verification**: Tests if database is running, port is accessible, and connectivity works
- **Admin access**: Uses the MyDB admin account (created automatically by the application)
- **Troubleshooting**: Get immediate access to investigate user-reported issues
- **Direct inspection**: Run queries, check tables, verify data without affecting user credentials

### Database Command Reference

If you're not familiar with database-specific commands, the MyDB documentation pages provide quick references:

- **PostgreSQL commands**: Navigate to **Documentation** → **Postgres** in the web UI
  - Lists common psql commands (`\l`, `\dt`, `\du`, `\d+`, etc.)
  - Shows how to create users, grant privileges, manage databases
  - Includes SQL queries for listing tables and schemas

- **MariaDB commands**: Navigate to **Documentation** → **MariaDB** in the web UI
  - Common MariaDB/MySQL commands
  - User and database management
  - Privilege grants and administration

**Quick command examples:**

```sql
-- PostgreSQL
\l                  -- List all databases
\dt                 -- List tables
\du                 -- List users/roles
\d+ tablename       -- Describe table structure
\c dbname           -- Connect to different database
\q                  -- Quit

-- MariaDB
SHOW DATABASES;     -- List all databases
SHOW TABLES;        -- List tables
SHOW GRANTS FOR user@host;  -- Show user privileges
USE dbname;         -- Switch to database
EXIT;               -- Quit
```

### Audit Feature

The **Audit** feature provides comprehensive reports on database container contents.

**How to use:**

1. Navigate to **Admin** → **Audit**
2. Select the database container to audit
3. View the generated report showing:
   - All users/roles and their privileges
   - All databases (excluding system databases)
   - All tables in each database
   - Row counts for each table

**Example Audit Report output:**

```
================================================================================
PostgreSQL Audit Report
Container: mydb_research_project
Host: mydb.yourorg.edu
Port: 32015
================================================================================

USERS AND ROLES:
--------------------------------------------------------------------------------
Role Name                      Superuser    CreateDB   CreateRole CanLogin
--------------------------------------------------------------------------------
mydbadmin                      True         True       True       True
researcher1                    False        False      False      True
researcher2                    False        False      False      True

DATABASES:
--------------------------------------------------------------------------------

Database: research_db
--------------------------------------------------------------------------------
Schema                         Table                                    Row Count
--------------------------------------------------------------------------------
public                         experiments                              1,234
public                         results                                  45,678
public                         metadata                                 89

Audit Complete
```

**Why this is useful:**

- **Quick overview**: See all database contents at a glance
- **Troubleshooting**: Verify databases and tables exist as expected
- **User support**: Help users understand their database structure
- **Capacity planning**: Check table sizes and row counts
- **Security**: Verify user permissions are correctly configured

### When to Use UI Admin Features vs. Command Line

**Use the Web UI Admin features when:**
- You need quick, visual information about a database
- User reports a problem and you need to verify database state
- You want to provide a connection command to a user
- You need a complete inventory of database contents

**Use command-line tools when:**
- You need to execute complex queries or scripts
- You're automating operations
- You need to access Docker/Swarm internals
- You're debugging the MyDB application itself

---

## Service Health Checks

### Check All Running Services

```bash
# List all Docker Swarm services
docker service ls

# Expected output should show:
# - mydb_dbaas (Flask application)
# - mydb_admin (Admin PostgreSQL database)
# - mydb_migrate (Migration PostgreSQL database)
```

### Check Specific MyDB Services

```bash
# Check dbaas service (Flask app)
docker service ps mydb_dbaas

# Check admin database service
docker service ps mydb_admin

# Check migration database service
docker service ps mydb_migrate
```

### Verify Service Health Status

```bash
# Detailed inspection of a service
docker service inspect mydb_dbaas --pretty

# Check number of replicas running
docker service ls --filter name=mydb_dbaas

# Should show: REPLICAS 1/1 (or your configured replica count)
```

---

## Docker Service Management

### View Service Details

```bash
# Get detailed JSON output
docker service inspect mydb_dbaas

# View service configuration
docker service inspect --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}' mydb_dbaas

# View environment variables
docker service inspect --format '{{json .Spec.TaskTemplate.ContainerSpec.Env}}' mydb_dbaas | jq
```

### List All User Database Services

```bash
# List all services with mydb_ prefix (user databases)
docker service ls --filter name=mydb_

# Count running database services
docker service ls --filter name=mydb_ --format "{{.Name}}" | wc -l
```

### Restart a Service

```bash
# Restart the Flask application
docker service update --force mydb_dbaas

# Restart admin database (careful - causes downtime!)
docker service update --force mydb_admin

# Restart a user database service
docker service update --force mydb_<container_name>
```

---

## Log Investigation

### View Service Logs

```bash
# View dbaas application logs (last 100 lines)
docker service logs mydb_dbaas --tail 100

# Follow logs in real-time
docker service logs -f mydb_dbaas

# View logs from last hour
docker service logs mydb_dbaas --since 1h

# View logs with timestamps
docker service logs mydb_dbaas --timestamps
```

### View Container Logs

```bash
# Find the container ID for a service
docker ps --filter "name=mydb_dbaas"

# View container logs directly
docker logs <container_id>

# Follow container logs
docker logs -f <container_id>

# View last 50 lines with timestamps
docker logs --tail 50 --timestamps <container_id>
```

### Search Logs for Errors

```bash
# Search for errors in mydb_dbaas logs
docker service logs mydb_dbaas 2>&1 | grep -i error

# Search for specific database errors
docker service logs mydb_dbaas 2>&1 | grep -i "postgres\|database"

# Export logs to file for analysis
docker service logs mydb_dbaas > /tmp/dbaas_logs_$(date +%Y%m%d_%H%M%S).log
```

---

## External Connectivity Testing

### Ping Flask Application

```bash
# From outside Docker Swarm (replace with your hostname/IP)
curl http://<DBAAS_HOST>:<PORT>/

# Check if application is responding
curl -I http://<DBAAS_HOST>:<PORT>/

# Test with timeout
curl --connect-timeout 5 http://<DBAAS_HOST>:<PORT>/
```

### Test Database Connectivity from Outside

```bash
# Test PostgreSQL connection (replace port with actual port)
nc -zv <DBAAS_HOST> 32010

# Or using telnet
telnet <DBAAS_HOST> 32010

# Test multiple database ports
for port in $(seq 32010 32020); do
  echo -n "Testing port $port: "
  nc -zv -w 2 <DBAAS_HOST> $port 2>&1 | grep -q succeeded && echo "OK" || echo "FAILED"
done
```

### Verify Service is Listening on Ports

```bash
# Check what ports are exposed by a service
docker service inspect mydb_<container_name> --format '{{json .Endpoint.Ports}}' | jq

# Check published ports on the host
netstat -tlnp | grep docker-proxy
```

---

## Direct Database Access

### Connect to User Database Service

```bash
# Find the service name
docker service ls --filter name=mydb_

# Execute psql inside a PostgreSQL container
docker exec -it $(docker ps -q -f name=mydb_<container_name>) \
  psql -U postgres -d <database_name>

# For MariaDB databases
docker exec -it $(docker ps -q -f name=mydb_<container_name>) \
  mariadb -u root -p

# For MongoDB databases
docker exec -it $(docker ps -q -f name=mydb_<container_name>) \
  mongosh --username root --authenticationDatabase admin
```

# Connect to the admin databases
```
source .env
PGPASSWORD="${MYDB_ADMIN_PASSWORD}"  psql --host sc-build-02 --port 32009 -d mydb_admin -U $MYDB_ADMIN_USER

# Migrate DB
PGPASSWORD="${MYDB_MIGRATE_PASSWORD}"  psql --host sc-build-02 --port 32008 -d mydb_admin -U $MYDB_ADMIN_USER
```

### Get Shell Access to a Container

```bash
# Get bash shell in dbaas Flask container
docker exec -it $(docker ps -q -f name=mydb_dbaas) /bin/bash

# Get shell in a PostgreSQL service
docker exec -it $(docker ps -q -f name=mydb_<container_name>) /bin/bash

# If bash not available, try sh
docker exec -it $(docker ps -q -f name=mydb_<container_name>) /bin/sh
```

### One-liner to Execute Commands

```bash
# Check PostgreSQL version in a user database
docker exec $(docker ps -q -f name=mydb_<container_name>) \
  psql -U postgres -c "SELECT version();"

# List databases in a PostgreSQL instance
docker exec $(docker ps -q -f name=mydb_<container_name>) \
  psql -U postgres -c "\l"

# Check MariaDB version
docker exec $(docker ps -q -f name=mydb_<container_name>) \
  mariadb -u root -p<password> -e "SELECT VERSION();"
```

---

## Storage and Volume Management

### List Docker Volumes

```bash
# List all Docker volumes
docker volume ls

# List volumes for MyDB (mydb_ prefix)
docker volume ls --filter name=mydb_

# Get detailed volume information
docker volume inspect mydb_<container_name>
```

### Check Volume Mount Points

```bash
# Inspect service volume configuration
docker service inspect mydb_<container_name> \
  --format '{{json .Spec.TaskTemplate.ContainerSpec.Mounts}}' | jq

# Check if volume exists
docker volume inspect mydb_<container_name> > /dev/null 2>&1 && \
  echo "Volume exists" || echo "Volume missing"
```

### Verify Volume Storage Service (NFS/Local)

```bash
# Check if volume storage path is accessible
ls -lah /mydb/postgres_dbs/

# Check NFS mounts (if using NFS)
mount | grep nfs

# Check disk space on volume storage
df -h /mydb/postgres_dbs/

# Check inode usage
df -i /mydb/postgres_dbs/
```

### Verify Docker Volume Driver

```bash
# Check volume driver
docker volume inspect mydb_<container_name> --format '{{.Driver}}'

# Expected: local or nfs (depending on configuration)
```

### Test Volume Write Access

```bash
# Create test container with volume mounted
docker run --rm -v mydb_<container_name>:/data alpine \
  sh -c "echo test > /data/test.txt && cat /data/test.txt"

# If successful, volume is writable
```

---

## Admin Database Queries

### Connect to mydb_admin Database

```bash
# Get the admin database container
docker ps --filter "name=mydb_admin"

# Connect to PostgreSQL admin database
docker exec -it $(docker ps -q -f name=mydb_admin) \
  psql -U postgres -d mydb_admin

# Alternative: Connect from host (if port is published - typically 32008)
psql -h <DBAAS_HOST> -p 32008 -U postgres -d mydb_admin
```

### Useful Admin Database Queries

Once connected to mydb_admin, use these queries:

```sql
-- List all active containers
SELECT c_id, name, dbengine, state
FROM container_state
WHERE state = 'running'
ORDER BY name;

-- Show container details
SELECT c.c_id, c.name, c.dbengine, c.data->>'Port' as port,
       c.data->>'owner' as owner, c.data->>'contact' as contact
FROM containers c
JOIN container_state cs ON c.c_id = cs.c_id
WHERE cs.state = 'running';

-- Count containers by database type
SELECT dbengine, COUNT(*) as count
FROM container_state
WHERE state = 'running'
GROUP BY dbengine;

-- View recent action log
SELECT c_id, name, action, description, ts
FROM action_log
ORDER BY ts DESC
LIMIT 20;

-- Check backup status
SELECT c_id, name, state, backup_id, ts, url
FROM backups
ORDER BY ts DESC
LIMIT 20;

-- Find containers by owner
SELECT c.name, c.dbengine, c.data->>'Port' as port
FROM containers c
JOIN container_state cs ON c.c_id = cs.c_id
WHERE c.data->>'owner' ILIKE '%username%'
  AND cs.state = 'running';

-- Show all table sizes in admin database
SELECT schemaname, tablename,
       pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- Count total active containers
SELECT COUNT(*) as total_active_containers
FROM container_state
WHERE state = 'running';
```

### Backup Admin Database Metadata

```sql
-- Export container metadata to CSV
\copy (SELECT c_id, name, dbengine, data FROM containers) TO '/tmp/containers_backup.csv' CSV HEADER;

-- Export container state to CSV
\copy (SELECT * FROM container_state) TO '/tmp/container_state_backup.csv' CSV HEADER;
```

---

## Common Troubleshooting Scenarios

### Service Won't Start

```bash
# Check service status
docker service ps mydb_dbaas --no-trunc

# Look for error messages in task history
docker service ps mydb_dbaas --format "{{.Error}}"

# Check recent logs
docker service logs mydb_dbaas --tail 100

# Verify configuration
docker service inspect mydb_dbaas --pretty
```

### Database Connection Failures

```bash
# Check if database port is exposed
docker service inspect mydb_<container_name> --format '{{json .Endpoint.Ports}}'

# Verify database is running inside container
docker exec $(docker ps -q -f name=mydb_<container_name>) \
  pg_isready -U postgres

# Check database logs
docker service logs mydb_<container_name> --tail 50
```

### Flask Application Not Responding

```bash
# Check if Flask process is running
docker exec $(docker ps -q -f name=mydb_dbaas) ps aux | grep python

# Test from inside container
docker exec $(docker ps -q -f name=mydb_dbaas) \
  curl http://localhost:5000/

# Check for port binding issues
docker exec $(docker ps -q -f name=mydb_dbaas) netstat -tlnp
```

### Out of Disk Space

```bash
# Check Docker system space usage
docker system df

# Clean up unused containers, images, volumes
docker system prune -a --volumes

# Check specific volume usage
docker system df -v | grep mydb_
```

---

## Emergency Procedures

### Restart All MyDB Services

```bash
# Restart Flask application
docker service update --force mydb_dbaas

# Wait for health check
sleep 10

# Verify it's running
docker service ps mydb_dbaas | grep Running
```

### Export All Container Metadata (Disaster Recovery)

```bash
# Backup admin database
docker exec $(docker ps -q -f name=mydb_admin) \
  pg_dump -U postgres mydb_admin > /tmp/mydb_admin_backup_$(date +%Y%m%d).sql

# Export to S3 (if configured)
aws s3 cp /tmp/mydb_admin_backup_*.sql s3://your-backup-bucket/disaster-recovery/
```

---

## Monitoring Commands (Quick Reference)

```bash
# Service status check
docker service ls --filter name=mydb_dbaas
docker service ls --filter name=mydb_admin
docker service ls --filter name=mydb_migrate

# Quick log check (all services)
docker service logs mydb_dbaas --tail 20
docker service logs mydb_admin --tail 20
docker service logs mydb_migrate --tail 20

# Container health
docker ps --filter name=mydb_dbaas
docker ps --filter name=mydb_admin
docker ps --filter name=mydb_migrate

# Volume check
docker volume ls --filter name=mydb_ | wc -l

# Disk space
df -h /mydb/
```

---

## Updating Application Code

When you make changes to the Flask application code, templates, or Python modules, you need to rebuild the Docker image and update the running service.

### Complete Update Workflow

```bash
# 1. Make your code changes
# Edit files in mydb/ directory (Python code, templates, static files)

# 2. Test locally (optional but recommended)
python -m mydb.test_postgres
python -m mydb.test_mariadb

# 3. Build new Docker image
./build_dbaas.sh

# This creates: dbaas:2.0.1 (or your current version)
# Verify the build succeeded:
docker images | grep dbaas

# 4. Tag the image for your registry
# Option A: Using DockerHub
docker tag dbaas:2.0.1 yourusername/dbaas:2.0.1

# Option B: Using a private registry
docker tag dbaas:2.0.1 your-registry.com/dbaas:2.0.1

# 5. Push to Docker registry
# Option A: DockerHub
docker push yourusername/dbaas:2.0.1

# Option B: Private registry
docker push your-registry.com/dbaas:2.0.1

# 6. Update the running service
# This pulls the new image and restarts the service
docker service update --image yourusername/dbaas:2.0.1 mydb_dbaas

# 7. Monitor the update
docker service ps mydb_dbaas

# 8. Verify the service is running with new code
docker service logs mydb_dbaas --tail 50 --follow
```

### Quick Update (Development)

For rapid iteration in development:

```bash
# Build and update in one step
./build_dbaas.sh && \
docker tag dbaas:2.0.1 yourusername/dbaas:2.0.1 && \
docker push yourusername/dbaas:2.0.1 && \
docker service update --image yourusername/dbaas:2.0.1 mydb_dbaas

# Monitor logs
docker service logs -f mydb_dbaas
```

### Rollback to Previous Version

If the update causes issues:

```bash
# View update history
docker service ps mydb_dbaas --no-trunc

# Rollback to previous version
docker service rollback mydb_dbaas

# Or manually specify a previous image version
docker service update --image yourusername/dbaas:2.0.0 mydb_dbaas
```

### Zero-Downtime Updates (Production)

For production updates with minimal downtime:

```bash
# Update with rolling update settings
docker service update \
  --update-parallelism 1 \
  --update-delay 10s \
  --image yourusername/dbaas:2.0.1 \
  mydb_dbaas

# This updates one replica at a time with 10 second delay
# Users experience minimal disruption
```

### Verify Update Success

```bash
# Check service is running
docker service ps mydb_dbaas | grep Running

# Test the web interface
curl -I http://your-host:5000/

# Check application version in logs
docker service logs mydb_dbaas 2>&1 | grep -i version

# Verify database connections still work
# Create a test database through the web UI
```

### Common Update Issues

#### Issue: Image pull fails

```bash
# Check registry authentication
docker login

# Or for private registry
docker login your-registry.com

# Verify image exists in registry
docker search yourusername/dbaas
```

#### Issue: Service doesn't restart

```bash
# Force update even if image appears unchanged
docker service update --force --image yourusername/dbaas:2.0.1 mydb_dbaas

# Check for errors
docker service ps mydb_dbaas --no-trunc
```

#### Issue: New code not reflected

```bash
# Verify image was actually rebuilt with new code
docker image inspect dbaas:2.0.1 | grep Created

# Check if old image is cached
docker image ls dbaas

# Remove old images and rebuild
docker image rm dbaas:2.0.1
./build_dbaas.sh
```

### Update Checklist

Before updating production:

- [ ] Code changes tested locally
- [ ] Database migrations applied (if any)
- [ ] Configuration changes documented
- [ ] Image built successfully
- [ ] Image pushed to registry
- [ ] Backup of admin database taken
- [ ] Update scheduled during low-usage period
- [ ] Rollback plan prepared
- [ ] Monitoring in place to detect issues

---

## Additional Resources

- See `CLAUDE.md` for application architecture details
- See `INSTALL.md` for initial deployment instructions
- See `TODO.md` for known issues and planned improvements
- See `BACKUPS.md` for backup configuration details
