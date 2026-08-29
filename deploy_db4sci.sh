#!/bin/bash
set -a
source .env
set +a

docker config create db4sci-init.sql - <<'EOF'
-- Create Role
CREATE ROLE ${admin} WITH LOGIN PASSWORD '${admin_pass}';
ALTER USER ${admin} WITH SUPERUSER;

-- Create Database
CREATE DATABASE "mydb_admin";

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE "mydb_admin" TO ${admin};
EOF

docker stack deploy -c db4sci.yml mydb 
