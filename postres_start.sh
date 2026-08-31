#!/bin/bash

# Start Postgres docker container for debug/testing

set -a
source .env
set +a

version='18.6'
name='psql-test'

# Run in background 
/usr/bin/docker run -d -p 32007:5432 \
   --name ${name} \
   -e POSTGRES_USER=${PG_ADMIN} \
   -e POSTGRES_PASSWORD="${PG_ADMIN_PASS}" \
   -e POSTGRES_DB="postgres" \
   postgres:${version}

echo To connect: "docker exec -it ${name}  psql -U \${PG_ADMIN} -d postgres"
echo Stop and remove: \"docker stop ${name}\; docker rm ${name}\"

