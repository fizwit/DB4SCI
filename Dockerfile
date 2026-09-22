# DBaas Container 
FROM python:3.11-slim

ENV FLASK_APP=mydb
ENV PYTHONUNBUFFERED=1
ENV TZ='America/Los_Angeles'

# DB4SCI run time environment = [prod, test, dev]
ENV DB4SCI_ENV=dev

# Update the system and install packages
#
# The Postgres client must be the newest major we deploy: pg_dump/pg_dumpall
# can dump any server at or below their own major version, but refuse a server
# newer than themselves.  Debian's own postgresql-client is too old (15 on
# bookworm), so pull the current client from the PGDG apt repo.  Bump the
# postgresql-client-NN version below whenever a newer Postgres major is adopted.
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive \
    apt-get -y --no-install-recommends install ca-certificates curl gnupg && \
    install -d /usr/share/postgresql-common/pgdg && \
    curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc && \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo $VERSION_CODENAME)-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive \
    apt-get -y --no-install-recommends install tzdata \
    libldap2-dev \
    libsasl2-dev \
    libssl-dev \
    libpq-dev \
    python3-dev \
    pkg-config \
    awscli \
    postgresql-client-18 \
    libmariadb-dev libmariadb-dev-compat mariadb-client \
    gcc \
    vim \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Create the sttrweb user and data directory
WORKDIR /app

RUN mkdir -p /data/dbs && \
    mkdir -p /data/db_backups 

RUN groupadd -f --gid 999 dbaas 
RUN useradd -u 999 -g 999 -s /bin/bash dbaas 
RUN useradd -u 1000 -g 999 -s /sbin/nologin docker

# Install Python packages

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt 


# Copy files to container
ADD *.py /app
COPY pyproject.toml /app/
ADD mydb /app/mydb/

# Setup cron for backups
COPY etc/mydb_backup.crontab /etc/cron.d/backup-cron
RUN chmod 0644 /etc/cron.d/backup-cron && \
    crontab -u dbaas /etc/cron.d/backup-cron && \
    touch /var/log/backup_all.log && \
    chown dbaas:dbaas /var/log/backup_all.log

# Switch to the server directory and start it up
COPY entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh
RUN chown -R dbaas:dbaas /app

# Expose port and run
EXPOSE 5008
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["flask", "run", "--host=0.0.0.0"]

