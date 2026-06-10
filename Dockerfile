# DBaas Container 
FROM python:3.11-slim

ENV FLASK_APP=mydb
ENV PYTHONUNBUFFERED=1
ENV TZ='America/Los_Angeles'

# DBAAS run time environment = [prod, test, dev]
ENV DBAAS_ENV=dev

# Update the system and install packages
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive \
    apt-get -y --no-install-recommends install tzdata \
    libldap2-dev \
    libsasl2-dev \
    libssl-dev \
    pkg-config \
    awscli \
    postgresql postgresql-contrib libpq-dev python3-dev \
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
ADD mydb /app/mydb/

# Setup cron for backups
COPY mydb_backup.crontab /etc/cron.d/backup-cron
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

