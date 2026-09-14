# MyDB Configuration Example
# Copy this file to mydb_config.py and customize for your environment:
# cp mydb/config.example mydb/mydb_config.py

import os

from .errors import AppError

# =============================================================================
# Organization Information
# =============================================================================
# Customize messages, email, and contact information
# Used throughout the web interface and email notifications

institutionName = "Fred Hutch"
supportOrgName = "SciComp"
supportOrgEmail = "scicomp@fredhutch.org"
supportPerson = "John Dey"
supportEmail = "jfdey@fredhutch.org"
backup_admin_mail = ["jfdey@fredhutch.org"]

# =============================================================================
# Branding - Logo and Favicon
# =============================================================================
# Path relative to mydb/static/ directory
# Replace with your organization's logo file

organizationLogo = "images/db4sci-logo.svg"
organizationFavicon = "favicon.ico"
backup_purge_period = "30" # used in documentation.html

# =============================================================================
# Secret Management
# =============================================================================
# Secrets are passed via Docker environment variables
# <TASK_TOKEN> is used to authenticate cron jobs, passed via http headers

AWS_BUCKET_NAME = os.environ.get("AWS_BUCKET_NAME")
FLASK_SECRET = os.environ.get("FLASK_SECRET")
FLASK_DEBUG = os.environ.get("FLASK_DEBUG", None)
SQLALCHEMY_ADMIN_URI = os.environ.get("SQLALCHEMY_ADMIN_URI")
SQLALCHEMY_MIGRATE_URI = os.environ.get("SQLALCHEMY_MIGRATE_URI")
DB4SCI_TASK_TOKEN = os.environ.get("DB4SCI_TASK_TOKEN")
DB4SCI_ENV = os.environ.get("DB4SCI_ENV")

# Postgres privileged account MyDB uses for backup/restore and for
# creating user databases.  Passed in by db4sci.yml.
PG_ADMIN = os.environ.get("PG_ADMIN")
PG_ADMIN_PASS = os.environ.get("PG_ADMIN_PASS")

# =============================================================================
# Docker Swarm Storage
# =============================================================================
# Backing store for the per-database docker volumes, set in .env.
# With SWARM_DRIVER=local / SWARM_OPTS=bind the volume is a bind mount, so
# SWARM_DEVICE must be reachable (and writable) from the DB4SCI container and
# from every swarm node that can run a database task -- in production that
# means shared NFS storage.

SWARM_DRIVER = os.environ.get("SWARM_DRIVER", "local").strip()
SWARM_TYPE = os.environ.get("SWARM_TYPE", "none").strip()
SWARM_OPTS = os.environ.get("SWARM_OPTS", "bind").strip()
SWARM_DEVICE = os.environ.get("SWARM_DEVICE", "/var/tmp/mydb").strip()

# =============================================================================
# Container Host Configuration
# =============================================================================
# Hostname and domain where MyDB and database containers run
# Users will connect to databases at: container_host.container_domain:PORT

container_host = "sc-build-02"
container_domain = "fredhutch.org"
s3_prefix_prod = "mydb"
s3_prefix_dev = "dev"
s3_prefix_migrate = "prod"
FQDN_host = container_host + "." + container_domain

# =============================================================================
# Active Directory / LDAP Authentication
# =============================================================================
# Configure your Active Directory server for user authentication
# The AD_auth.py module can be replaced with alternative authentication

ADServer = "dc.fhcrc.org"
ADDomain = "fhcrc.org"
ADSearchBase = "dc=fhcrc,dc=org"

# =============================================================================
# Application Settings
# =============================================================================

# Base port for database allocation (automatically increments from here)
base_port = 32010

# Docker configuration
docker = "/usr/bin/docker"
base_url = "unix://var/run/docker.sock"

# List of administrator usernames (AD usernames)
# Admins have access to /admin/* routes
admins = ["jfdey"]

# Timezone for container operations
TZ = os.getenv("TZ", "America/Los_Angeles")

# AWS CLI path
aws = "aws"

# =============================================================================
# Directory Paths
# =============================================================================
# Application root directory
db4sci_path = "/opt/db4sci"

# protect the `backup_all` view with a token
BACKUP_TOKEN = 'secretKey'

# =============================================================================
# Database Engine Configuration
# =============================================================================
# Configuration for each supported database type
# Images are listed in display order - first image is the default

dbs = {  # Database engine configuration
    "Postgres": {
        "default_port": 5432,
        "backupdir": "/var/lib/postgresql/backup",
        "mapped_volume": "",
        "command": "postgres",
        "service_user": "postgres",
        "V": [  # Versions
            {"version": "18.6",
                "image": "postgres:18.6",
                "volume": "/var/lib/postgresql/"},
            {"version": "17.4",
                "image": "postgres:17.4",
                "volume": "/var/lib/postgresql/data"},
            {"version": "13.2",
                "image": "postgres:13.2",
                "volume": "/var/lib/postgresql/data"},
        ],
    },
    "MariaDB": {
        "default_port": 3306,
        "command": "mysqld",
        "service_user": "root",
        "mapped_volume": "/var/lib/mysql",
        "V": [  # Versions
            {"version": "12.0.2",
                "image": "mariadb:12.0.2",
                "volume": "/var/lib/mysql"},
        ],
    },
    "MongoDB": {
        "default_port": 27017,
        "pub_ports": [27017],
        "backupdir": "/var/backup",
        "mapped_volume": "/data/db",
        "service_user": "mongodb",
        "command": "mongod",
        "dbengine": "MongoDB",
        "V": [  # Versions
            {"version": "8.2.2",
                "image": "mongo:8.2.2",
                "volume": "/data/db"},
        ],
    },
    # Uncomment to enable Neo4j support
    # "Neo4j": {
    #    "pub_ports": [7473, 7687],  # 7473:HTTPS  7687:Bolt
    #    "backupdir": "/backup",
    #    "volumes": [
    #        ["DBVOL", "/data", "/data"],
    #        ["DBVOL", "/logs", "/logs"],
    #        [backupvol, "/", "/backup"],
    #        ["", "/opt/eb4sci/neo4j/ssl", "/ssl"],
    #    ],
    #    "command": "neo4j",
    #    "dbengine": "Neo4j",
    #    "mapped_volume": "/data",
    #    "V": [  # Versions
    #        {"version": "3.2.5",
    #            "image": "neo4j:3.2.5",
    #            "volume": "/data"},
    #    ],
    # },
}

dbtypes = dbs.keys()


def default_image(dbengine):
    """Image the create form pre-selects: the first entry in ["V"]."""
    return dbs[dbengine]["V"][0]["image"]


def mapped_volume(dbengine, image):
    """Container path the data volume is mounted on for `image`.

    The path is per version because the upstream images move it: Postgres 18
    relocated PGDATA to /var/lib/postgresql/<major>/docker and moved the
    image's VOLUME up to /var/lib/postgresql, while 17 and older keep the
    cluster in /var/lib/postgresql/data.  Mounting the wrong path does not
    error -- the volume is left empty and the database initializes into the
    container's writable layer, where it is lost on reschedule.

    Falls back to the engine-level "mapped_volume" for an image that is not
    listed (a container migrated from v1 can name an image we no longer offer).
    """
    for version in dbs[dbengine]["V"]:
        if version["image"] == image:
            if version.get("volume"):
                return version["volume"]
            break
    fallback = dbs[dbengine].get("mapped_volume")
    if not fallback:
        raise AppError(
            f"No volume path for {dbengine} image {image}: add it to "
            f'dbs["{dbengine}"]["V"] in mydb_config.py'
        )
    return fallback

# =============================================================================
# Metadata Fields
# =============================================================================
# Metadata fields from version 1 of MyDB
# Used when creating container labels for database services

mydb_v1_meta_data = [
    "username",
    "dbuser",
    "image",
    "description",
    "department",
    "manager",
    "owner",
    "contact",
    "app_name",
    "backup_freq",
]

# =============================================================================
# Log File Paths
# =============================================================================

backup_log = "/mydb/logs/backup.log"
admindb_log = "/mydb/logs/admindb.log"

# =============================================================================
# Database Admin Accounts
# =============================================================================
# For every database type, MyDB creates a privileged admin account
# used for backup operations. Set usernames and passwords here.
#
# SECURITY WARNING: These passwords should be strong and unique.
# In production, use Docker secrets or environment variables instead
# of hardcoding passwords in this file.

accounts = {
    "Postgres": {
        # Moved to docker env vars, see PG_ADMIN/PG_ADMIN_PASS above.
        "admin": PG_ADMIN,
        "admin_pass": PG_ADMIN_PASS,
    },
    "MongoDB": {
        "admin": "dbaas",
        "admin_pass": "fhmongoadmin",
    },
    "MariaDB": {
        "admin": "root",
        "admin_pass": "fhmariaadmin",
    },
    "Neo4j": {
        "admin": "neo4j",
        "admin_pass": "fhneo4jadmin",
    },
    # MyDB admin database credentials
    "admindb": {
        "admin": "mydbadmin",
        "admin_pass": "db4docker@25",
        "v1_admin_pass": "db4docker",
        "contact": "jfdey@fredhutch.org",
        "owner": "John Dey",
    },
    # Test user credentials (used by test scripts)
    "test_user": {
        "admin": "tester",
        "admin_pass": "CHANGE_ME_TEST_PASSWORD",
        "contact": "test@yourorg.edu",
        "owner": "Test User",
    },
}

# =============================================================================
# Email Configuration
# =============================================================================
# SMTP settings for sending email notifications

MAIL_FROM = "scicomp_srv@fredhutch.org"
MAIL_TO = "jfdey@fredhutch.org"
MAIL_SERVER = "mx.fhcrc.org"
