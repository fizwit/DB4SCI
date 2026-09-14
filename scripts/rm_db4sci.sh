#!/bin/bash

# remove docker service and assicated objects
# used when testing, for shutdown and starting

if [[ $# == 1 ]]; then
   name=$1
else
   name=db4sci
fi

echo -n 'Service: '
docker service rm mydb_${name}
sleep 4
echo -n 'Config: ' 
docker config rm  mydb_${name}_init.sql
echo -n 'Volume: ' 
docker volume rm mydb_${name}

