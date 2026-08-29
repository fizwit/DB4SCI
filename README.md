# DB4Sci - Database as a Service for Scientists

DB4Sci is a self-service database provisioning platform that enables researchers to create and manage their own database instances on demand. Built on Docker Swarm, DB4Sci automates database lifecycle management including creation, backups, and monitoring.

## Key Features

### On-Demand Database Provisioning
Scientists can instantly create their own database instances through a simple web interface without requiring IT intervention:
- **PostgreSQL** - Relational database with full SQL support
- **MariaDB** - MySQL-compatible relational database
- **MongoDB** - Document-oriented NoSQL database

Each database instance is:
- Isolated in its own Docker Swarm service
- Configured with user-specified credentials
- Automatically allocated a unique port
- Backed by persistent Docker volumes

### Automated Backup Management
DB4Sci handles all backup operations automatically:
- Scheduled automated backups to AWS S3
- User-initiated on-demand backups via web UI
- Complete backup history and audit trail
- Point-in-time recovery capabilities
- Database-specific backup strategies (pg_dump, mariadb-dump, mongodump)

### User-Friendly Web Interface
- Create databases with a simple single form
- View all your running database instances
- Initiate backups and restores
- Monitor database status and uptime
- Manage database lifecycle (restart, delete)

### Enterprise Features
- Active Directory authentication (modular - can be replaced with other auth methods)
- Metadata tracking for all database instances
- Role-based access control (user vs admin)
- Email notifications for database events
- Audit logging for all operations

## Architecture

DB4Sci is a Python Flask application that manages Docker Swarm services. Each database instance runs as an independent Swarm service with:
- **Persistent storage** via Docker volumes
- **Initialization scripts** via Docker configs
- **Isolated networking** with exposed ports
- **Automated restarts** and health monitoring

The application maintains its own metadata database (PostgreSQL or SQLite) to track:
- All database instances (active and historical)
- Current state and configuration
- Backup history and S3 locations
- User actions and audit logs

## Technology Stack

- **Backend**: Python 3, Flask
- **Container Orchestration**: Docker Swarm
- **Database Support**: PostgreSQL, MariaDB, MongoDB
- **Authentication**: Active Directory/LDAP (modular)
- **Storage**: AWS S3 for backups, Docker volumes for data
- **Admin Database**: PostgreSQL

## Quick Start

See [INSTALL.md](INSTALL.md) for complete installation instructions.

## Documentation

- [Installation Guide](INSTALL.md) - Setup and deployment
- [User and Application Backup Notes](BACKUPS.md) - Backup and Restore of user and Admin meta data 
- [CLAUDE.md](CLAUDE.md) - AI-assisted development guide
- [Release Notes](RELEASE_NOTES.md) - Project history

## Use Cases

DB4Sci is designed for research environments where:
- Scientists need temporary or project-specific databases
- Self-service database provisioning reduces IT bottlenecks
- Automated backups ensure data safety without manual intervention
- Database instances can be created, used, and decommissioned on demand
- Audit trails and metadata tracking are required for compliance

## Authentication

DB4Sci uses Active Directory for user authentication by default. The authentication module (`mydb/AD_auth.py`) is self-contained and can be replaced with alternative authentication methods (OAuth, SAML, local accounts, etc.) without modifying core application logic.

## License

[Include your license information here]

## Support

[Include support contact information here]
