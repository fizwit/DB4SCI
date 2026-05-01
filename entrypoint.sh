#!/bin/bash
set -e

# Start cron in the background
echo "Starting cron daemon..."
cron

# Load AWS credentials from Docker secrets
export MYDB_AWS_ACCESS_KEY_ID=${MYDB_AWS_ACCESS_KEY_ID}
export MYDB_AWS_SECRET_ACCESS_KEY=${MYDB_AWS_SECRET_ACCESS_KEY}
export MYDB_AWS_BUCKET_NAME=${MYDB_AWS_BUCKET_NAME}
export MYDB_AWS_DEFAULT_REGION=us-west-2 

# Pass environment variables to cron jobs
printenv | grep -v "no_proxy" > /etc/environment

# Execute the main command (Flask app)
echo "Starting Flask application..."
exec "$@"
