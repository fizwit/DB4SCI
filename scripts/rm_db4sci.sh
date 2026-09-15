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
echo -n 'Config: ' 
docker config rm  mydb_${name}_init.sql
sleep 4
status=$(docker volume rm mydb_${name} 2>&1)
echo "Volume: ${status}" 
while [[ ${status} == *"volume is in use"* ]]; do
    sleep 2
    status=$(docker volume rm mydb_${name} 2>&1)
    echo "Volume: ${status}" 
done
