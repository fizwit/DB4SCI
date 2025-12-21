# MyDB Installation Guide

This guide covers the installation and deployment of MyDB in a Docker Swarm environment.

## Prerequisites

- Docker Engine with Swarm mode enabled
- Docker Compose
- Python 3.12+
- Access to AWS S3 for backups
- Active Directory server (or alternative authentication provider)
- PostgreSQL for admin database

## System Requirements

### Docker Swarm Setup

MyDB requires Docker Swarm to be initialized on your host:

```bash
docker swarm init
```

Verify Swarm is running:
```bash
docker node ls
```

### Network Configuration

MyDB assumes it has a dedicated DNS namespace for your organization. Create a DNS entry for the application (e.g., `db4sci.yourorg.edu`).

Docker Swarm will map the Flask application to standard HTTP/HTTPS ports (80/443), and all database instances will be accessible from this common URL with unique port numbers:

```
Web Interface:  https://db4sci.yourorg.edu
Database 1:     db4sci.yourorg.edu:32010
Database 2:     db4sci.yourorg.edu:32011
Database N:     db4sci.yourorg.edu:320xx
```

Port range 32010-32999 should be available on your host. This range is automatically managed by MyDB and does not require configuration.

## Installation Steps

### 1. Configuration

#### Configure Application Settings

Copy the example configuration file and edit for your environment:

```bash
cp mydb/config.example mydb/mydb_config.py
```

Edit `mydb/mydb_config.py` to configure:
- Organization name and support contacts
- Container host and domain
- Base port for database allocation
- AWS S3 bucket information
- Active Directory server settings
- Admin user list

#### Configure Environment Variables

Copy the environment template and configure secrets:

```bash
cp env_example .env
```

Edit `.env` and set:
- `FLASK_SECRET` - Flask session secret key
- `SQLALCHEMY_ADMIN_URI` - Admin database connection string
- `AWS_ACCESS_KEY_ID` - AWS credentials for S3 backups
- `AWS_SECRET_ACCESS_KEY` - AWS secret key
- `AWS_BUCKET_NAME` - S3 bucket name
- `MAIL_TO` - Email recipients for notifications
- Active Directory configuration (if applicable)

### 2. Create Docker Secrets

Create Docker Swarm secrets from your `.env` file:

```bash
./dbaas_secrets.sh
```

This script creates secrets for:
- Flask secret key
- Database connection strings
- AWS credentials
- Email configuration

### 3. Customize Branding (Optional)

Customize MyDB with your organization's contact information and optionally replace the default DB4Sci logo with your own branding.

#### Update Organization Information

Edit `mydb/mydb_config.py` (lines 25-32) to customize contact information and support details:

```python
# Organization
organizationName = "Your Organization"
supportOrganization = "IT Support Team"
supportEmail = "support@yourorg.org"
supportPerson = "Your Name"
supportAdmin = ["admin@yourorg.org"]
backup_admin_mail = ["backup-alerts@yourorg.org"]
```

#### Replace Logo (Optional)

The default DB4Sci logo appears in the header banner. To use your organization's logo:

1. **Copy your logo file** to `mydb/static/images/`:
   ```bash
   cp your-logo.png mydb/static/images/your-logo.png
   ```

2. **Update the logo path** in `mydb/mydb_config.py` (lines 34-38):
   ```python
   # Branding - Logo and Favicon
   # Path relative to mydb/static/ directory
   organizationLogo = "images/your-logo.png"
   ```

Logo recommendations:
- Format: PNG or SVG with transparent background
- Aspect ratio: Wide/horizontal orientation works best
- Recommended dimensions: 200-400px wide for PNG

#### Replace Favicon (Optional)

To use a custom favicon in the browser tab:

```bash
cp your-favicon.ico mydb/static/favicon.ico
```

Favicon requirements: `.ico` format, 16x16 or 32x32 pixels

### 4. Prepare Docker Compose Configuration

Generate the resolved Docker Compose file with environment variables:

```bash
envsubst < dbaas.yml > dbaas_resolved.yml
```

Review `dbaas_resolved.yml` to ensure all variables are correctly substituted.

### 5. Build the MyDB Container

Build the Docker image:

```bash
./build_dbaas.sh
```

This creates the `dbaas:2.0.1` image with all dependencies.

### 5. Tag and Push to Registry

Tag the image for your Docker registry:

```bash
docker tag dbaas:2.0.1 your-registry/dbaas:2.0.1
docker push your-registry/dbaas:2.0.1
```

**Note**: Docker Swarm requires images to be available in a registry accessible from all nodes.

### 6. Admin Databases

The admin databases must be up and running before the application service can
be started. 

```bash
  ./deploy_admin.sh
  # verify
  docker service ls --filter name=mydb
```

### 7. Deploy to Docker Swarm

Deploy the MyDB stack:

```bash
docker stack deploy --detach -c dbaas_resolved.yml mydb
```

Verify deployment:

```bash
docker stack services mydb
docker service ls | grep mydb
```

## Post-Installation

### Verify Installation

1. **Check service status:**
   ```bash
   docker service ps mydb_dbaas
   ```

2. **Access the web interface:**
   Navigate to `http://your-host:port` (default port 5000)

3. **Test authentication:**
   Log in with an Active Directory account

4. **Create a test database:**
   Use the web UI to create a test PostgreSQL database

### Update Deployed Service

To update the service with a new image without redeploying:

```bash
docker service update --force --image dbaas:2.0.1 mydb_dbaas
```

**Note:** This same process is used when updating application code. After making code changes, rebuild the image (step 5), tag and push to registry (step 5), then update the service with the new image. See the "Updating Application Code" section in [OPERATIONS.md](OPERATIONS.md) for the complete workflow.

### Debugging

View environment variables in the running container:

```bash
docker exec -it $(docker ps -q -f "label=com.docker.swarm.service.name=mydb_dbaas") env | grep -i SQLALCHEMY
```

View service logs:

```bash
docker service logs mydb_dbaas --tail 100 --follow
```

## Directory Structure

After installation, MyDB expects the following directories (configured in `mydb_config.py`):

- `/mydb_admin/db_backups` - Backup storage (shared volume)
- `/opt/dbaas` - Application root (optional)

These directories are typically mounted as Docker volumes.

## Database Engine Images

### Configuring Available Database Versions

Database engine versions are configured in `mydb/mydb_config.py` in the `info` dictionary. Multiple versions of each engine can be supported simultaneously, allowing users to select their preferred version when creating a database.

Example configuration from `mydb_config.py`:

```python
info = {
    "Postgres": {
        "images": [
            ["Postgres 17.4", "postgres:17.4"],  # First entry is default
            ["Postgres 13.2", "postgres:13.2"],
        ],
    },
    "MariaDB": {
        "images": [
            ["MariaDB 12.0.2", "mariadb:12.0.2"],
        ],
    },
    "MongoDB": {
        "images": [
            ["MongoDB 8.2.2", "mongo:8.2.2"],
        ],
    },
}
```

The first image in each list is the default selection in the web interface.

### Pre-pulling Images (Recommended)

**Important**: All images listed in `mydb_config.py` must be available on your Docker Swarm nodes before users can create databases with those versions.

While Docker Swarm will attempt to pull images automatically when creating services, it is **strongly recommended** to pre-pull images manually to:
- Ensure availability and avoid service creation failures
- Verify image compatibility
- Re-tag images if needed (e.g., removing platform suffixes like `-noble`)

Example pre-pull workflow:

```bash
# Pull official images
docker pull postgres:17.4
docker pull mariadb:12.0.2
docker pull mongo:8.2.2-noble

# Re-tag if needed (remove platform suffix)
docker tag mongo:8.2.2-noble mongo:8.2.2

# Verify images match mydb_config.py
docker images | grep -E 'postgres|mariadb|mongo'
```

**Critical**: Image names in `mydb_config.py` must exactly match the tags available on your Swarm nodes. For example, if you configure `"mongo:8.2.2"` in `mydb_config.info['MongoDB']['images']`, that exact tag must exist on the Swarm node.

## Security Considerations

1. **Secrets Management**: All sensitive credentials should be stored as Docker secrets, not in configuration files
2. **Network Isolation**: Consider using Docker overlay networks to isolate database services
3. **Access Control**: Configure firewall rules to restrict access to database ports
4. **S3 Bucket**: Use IAM roles with minimal required permissions for S3 access
5. **HTTPS**: Deploy a reverse proxy (nginx, traefik) with SSL/TLS for production use

## Troubleshooting

### Service Fails to Start

Check logs for errors:
```bash
docker service logs mydb_dbaas --tail 50
```

Common issues:
- Missing or incorrect environment variables
- Database connection failures
- Docker socket permission issues

### Database Creation Fails

1. Verify Docker Swarm is functioning:
   ```bash
   docker service ls
   docker node ls
   ```

2. Check available disk space for volumes:
   ```bash
   docker system df
   ```

3. Review service logs:
   ```bash
   docker service logs mydb_dbaas
   ```

### Authentication Issues

1. Verify AD server connectivity from container
2. Check AD credentials in secrets
3. Review authentication logs in application logs

## Upgrade Procedure

1. Build new image with updated version tag
2. Tag and push to registry
3. Update stack with new image version:
   ```bash
   docker service update --image your-registry/dbaas:NEW_VERSION mydb_dbaas
   ```

## Backup and Recovery

### Automated Nightly Backups

MyDB includes automated backup capabilities for all user database instances. Backups are configured on an external cron server and stream directly to AWS S3.

**See [BACKUPS.md](BACKUPS.md) for complete backup configuration and scheduling instructions.**

### MyDB Admin Database

The MyDB admin database is critical infrastructure that should be backed up nightly. This database contains:

- **Container metadata** - All database instance configurations and state
- **Backup logs** - History of all backup operations
- **Action logs** - Audit trail of all user and admin actions
- **Migration data** - Required for redeployment and version upgrades

**Backup the admin database nightly:**

```bash
# Stream directly to S3 (recommended)
PGPASSWORD='admin_password' pg_dump -h admin-db-host -U mydbadmin -F c mydb_admin | \
  aws s3 cp - s3://your-bucket/admin_db/mydb_admin_$(date +\%Y\%m\%d).dump

# Or save locally
pg_dump -h admin-db-host -U mydbadmin -F c mydb_admin > mydb_admin_$(date +\%Y\%m\%d).dump
```

**Automated admin database backup cron:**

```cron
# Backup admin database at 1:00 AM (before user database backups at 2:00 AM)
0 1 * * * PGPASSWORD='password' pg_dump -h admin-db-host -U mydbadmin -F c mydb_admin | aws s3 cp - s3://bucket/admin_db/mydb_admin_$(date +\%Y\%m\%d).dump 2>&1 | logger -t mydb_admin_backup
```

### Admin Database Recovery

The admin database must be restored before redeploying MyDB or when using the **Migrate** feature:

```bash
# Restore from S3
aws s3 cp s3://your-bucket/admin_db/mydb_admin_20250101.dump - | \
  pg_restore -h admin-db-host -U mydbadmin -d mydb_admin

# Or restore from local file
pg_restore -h admin-db-host -U mydbadmin -d mydb_admin mydb_admin_20250101.dump
```

**Important:** The admin database is essential for:
- Redeploying MyDB to a new environment
- Upgrading MyDB to a new version
- Using the Migrate feature to recreate database services
- Recovering container metadata and backup history

## Uninstall

To remove MyDB:

```bash
# Remove stack
docker stack rm mydb

# Remove secrets
docker secret rm $(docker secret ls -q -f "name=mydb_")

# Remove volumes (WARNING: This deletes all database data)
docker volume rm $(docker volume ls -q -f "name=mydb_")
```

## Support

For issues and questions:
- Check logs: `docker service logs mydb_dbaas`
- Review [development.md](development.md) for architecture details
- Contact your MyDB administrator

## Next Steps

After installation:
1. Review [CLAUDE.md](CLAUDE.md) for development guidance
2. Configure automated backup schedules
3. Set up monitoring and alerting
4. Train users on the web interface
