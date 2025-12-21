# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MyDB is a Python Flask application that creates and manages containerized database instances (DBaaS - Database as a Service). It provides a web interface for users to provision, manage, backup, and delete database containers (PostgreSQL, MariaDB, MongoDB, Neo4j) running in Docker.

## Architecture

### Core Components

**Application Layer**
- `mydb/__init__.py` - Flask app factory with initialization logic
- `mydb/mydb_views.py` - Flask routes and web UI handlers
- `webui.py` - Flask application entry point, calls `mydb_setup()` then starts Flask

**Database Management**
- `mydb/admin_db.py` - Admin database operations using SQLAlchemy with SQLite/PostgreSQL backend
- `mydb/models.py` - SQLAlchemy models: Containers, ContainerState, ActionLog, Backups, Labels
- `mydb/container_util.py` - Docker container lifecycle operations (create, stop, restart, delete)
- `mydb/postgres_util.py` - PostgreSQL-specific container operations
- `mydb/mariadb_util.py` - MariaDB-specific operations
- `mydb/mongodb_util.py` - MongoDB-specific operations
- `mydb/neo4j_util.py` - Neo4j-specific operations

**Configuration**
- `mydb/mydb_config.py` - Centralized configuration: ports, volumes, database images, admin accounts, email settings

**Authentication**
- `mydb/AD_auth.py` - Active Directory/LDAP authentication

### Data Flow for Container Creation

1. User submits form via web UI (`mydb_views.py::create_form()`)
2. Route handler calls database-specific create function (e.g., `postgres_util.create()`)
3. Create function:
   - Validates container name doesn't exist
   - Allocates port using `container_util.get_max_port()`
   - Creates directories via `container_util.make_dirs()`
   - Calls `container_util.create_con()` which:
     - Runs Docker container with configured volumes, ports, environment
     - Adds record to Containers table via `admin_db.add_container()`
     - Adds to ContainerState table via `admin_db.add_container_state()`
     - Logs action via `admin_db.add_container_log()`
4. Database-specific post-creation (e.g., creating admin accounts for backups)

### Admin Database (State Tracking)

The admin database tracks all containers using these tables:
- **Containers** - Immutable record of every container created, stores Docker inspect data as JSONB
- **ContainerState** - Tracks current state of active containers (running, maintenance, etc.)
- **ActionLog** - Audit log of all container events (create, delete, restart, backup)
- **Backups** - Backup execution history with S3 URLs and status

All container operations update both Docker and the admin database atomically.

### Container Lifecycle

**Create**: `create_con()` → Docker run → Add to Containers table → Add to ContainerState → Log creation
**Delete**: Authenticate user → `stop_remove()` → Delete from ContainerState → Log deletion → Clean up directories
**Restart**: Authenticate user → Docker restart → Log restart
**Backup**: Create backup directory → pg_dump/mongodump → Upload to S3 → Log to Backups table

### Volume Management

Containers use Docker volumes for persistent storage:
- **Data volume**: `/mydb/postgres_dbs`, `/mydb/storcrawldb`, or encrypted volumes (see `data_volumes` in config)
- **Backup volume**: `/mydb/db_backups` (shared across all containers)
- Volume paths are configurable per database type in `mydb_config.info[dbtype]['volumes']`

## Development Commands

### Build and Run

```bash
# Build Docker image
./build_dbaas.sh

# Run with docker-compose
docker-compose up

# Run standalone container
./run.sh

# Start container (non-interactive)
./start.sh
```

### Environment Setup

Required environment variables (see `docker-compose.yml` or `build_dbaas.sh`):
- `FLASK_SECRET` - Flask session secret
- `DBAAS_HOST`, `DBAAS_DOMAIN`, `DBAAS_IP` - Host configuration
- `SQLALCHEMY_ADMIN_URI` - Admin database connection string
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_BUCKET` - S3 backup credentials
- `ADSERVER`, `ADDOMAIN` - Active Directory authentication
- `MAIL_TO` - Email notification recipients

### Testing

```bash
# Run database-specific tests
python -m mydb.test_postgres
python -m mydb.test_mariadb
python -m mydb.test_mongodb
python -m mydb.test_neo4j

# Test container utilities
python -m mydb.test_container

# Test admin database directly
python -m mydb.admin_db --state              # Show container state
python -m mydb.admin_db --active             # List active containers
python -m mydb.admin_db --info <name>        # Show container info
python -m mydb.admin_db --show_event_logs    # Show event log
```

### PostgreSQL Operations

```bash
# Clone existing container
python -m mydb.postgres_util --clone <container_name>

# Clone and update to latest PostgreSQL version
python -m mydb.postgres_util --clone <container_name> --update-clone

# Clone with custom shared memory
python -m mydb.postgres_util --clone <container_name> --shm_size 2G
```

### Compose Template Generation

```bash
# Generate docker-compose.yml and init SQL for a container
python -m mydb.build_yml <container_name>
```

## Important Implementation Details

### Port Allocation
- Base port: 32010 (configured in `mydb_config.base_port`)
- `get_max_port()` scans all running containers and returns highest port + 1
- Ports are bound to specific IP (`mydb_config.container_ip`)

### Authentication
- Users authenticate via Active Directory (LDAP) - see `AD_auth.py`
- Container operations (restart, delete) require database password authentication
- Admin users listed in `mydb_config.admins` have additional privileges

### Backup Strategy
- PostgreSQL: `pg_dumpall -g` for roles + `pg_dump -F c` per database
- Backups stored in timestamped directories: `/mydb/db_backups/<container>/<container>_YYYY-MM-DD_HH-MM-SS/`
- S3 upload URLs logged to Backups table
- `postgresql.auto.conf` backed up for restore purposes

### Docker Integration
- Application runs inside Docker container with `/var/run/docker.sock` mounted
- Uses `docker` Python SDK to manage child containers
- Container labels store metadata (owner, contact, backup frequency, support level, PHI status)

### Database Engine Configuration
All database types share common configuration pattern in `mydb_config.info`:
- `pub_ports` - Exposed ports
- `volumes` - Volume mappings (supports 'DBVOL' placeholder)
- `command` - Container command
- `images` - Available image versions (first is default)
- `backupdir` - Backup directory inside container

## Version 2 Migration Notes (from Notes.md)

The codebase is transitioning to:
- Docker Swarm deployment
- Python 3 (completed)
- SQLite for metadata (or PostgreSQL via SQLALCHEMY_ADMIN_URI)
- Docker volumes for persistent storage
- Removal of DB-based utilities from application
- YAML-based container definitions

PostgreSQL-specific notes:
- `POSTGRES_DB` defined by user
- `POSTGRES_USER`, `POSTGRES_PASSWORD` defined by application for backup purposes
- User accounts created via `/docker-entrypoint-initdb.d` scripts
- Startup scripts only run if database directory is empty

## Admin Web Interface Routes

Standard user routes:
- `/` or `/index` - Dashboard
- `/login`, `/logout` - Authentication
- `/list_containers/` - View active containers
- `/create_form/?dbtype=<type>` - Container creation form
- `/select_container/?dbaction=<action>` - Select container for backup/list_s3
- `/select_with_auth/?dbaction=<action>` - Select container for restart/delete

Admin-only routes (require admin user in `mydb_config.admins`):
- `/admin/help` - Admin command reference
- `/admin/state` - Container state table
- `/admin/list` - Active containers
- `/admin/docker_ps` - Docker ps output
- `/admin/containers` - All containers summary
- `/admin/log` - Action log
- `/admin/info?name=<name>` or `?cid=<id>` - Container info
- `/admin/data?cid=<id>` - Docker inspect data
- `/admin/update?cid=<id>&key=value` - Update container info
- `/admin/delete?dbname=<name>` - Force delete container
- `/admin/email_list` - Generate user email list JSON
- `/admin/backup_audit` - Backup audit report
- `/admin/setMaintenance[?done]` - Toggle maintenance state

## Database Schema

When modifying models in `mydb/models.py`, the schema auto-creates on startup via `admin_db.init_db()` which calls `Base.metadata.create_all()`. No manual migrations are currently implemented.

## Multi-Database Support

The application supports connecting to two separate admin databases with identical schemas:
- **admin_db** - Production admin database (full CRUD operations)
- **migrate_db** - Migration/staging admin database (read-only query operations)

### Configuration

Set environment variables for database connections:
```bash
export SQLALCHEMY_ADMIN_URI="postgresql://user:pass@host:port/mydb_admin"
export SQLALCHEMY_MIGRATE_URI="postgresql://user:pass@host:port/mydb_migrate"
```

### Usage Patterns

**admin_db (Production - Full CRUD)**
```python
from mydb import admin_db

# Full CRUD operations available
containers = admin_db.list_container_names()
state = admin_db.get_container_state('container_name')
admin_db.add_container_log(c_id, 'name', 'action', 'description')
```

**migrate_db (Migration - Read-only queries)**
```python
from mydb import migrate_db

# Query operations only
containers = migrate_db.list_container_names()
state = migrate_db.get_container_state('container_name')
(header, body) = migrate_db.display_active_containers()
data = migrate_db.get_container_data('container_name')
info = migrate_db.display_container_info('container_name')
(header, body) = migrate_db.display_containers()
(header, body) = migrate_db.display_email_list()
backups = migrate_db.backup_lastlog(c_id)
```

**Initialize database schemas**
```python
admin_db.init_db()    # Initialize production admin database
migrate_db.init_db()  # Initialize migrate admin database
```
