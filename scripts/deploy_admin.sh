#!/bin/bash
set -a
source .env
set +a

# Start the admin_db services

set -a
source .env
set +a
prefix=mydb
mydb_services='admin_db migrate_db'

for service in $mydb_services; do
    status=`docker service inspect ${prefix}_${service}`
    if [[ $? -ne 0 ]]; then
      echo Starting $service
      docker stack deploy -c ${service}.yml $prefix 
    fi
done

