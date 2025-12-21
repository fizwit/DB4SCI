#!/bin/bash
set -e

# Start cron in the background
echo "Starting cron daemon..."
cron

# Load AWS credentials from Docker secrets
export AWS_ACCESS_KEY_ID=$(cat /run/secrets/mydb_mydb_aws_access_key_id)
export AWS_SECRET_ACCESS_KEY=$(cat /run/secrets/mydb_mydb_aws_secret_access_key)
export AWS_BUCKET_NAME=$(cat /run/secrets/mydb_mydb_aws_bucket_name)
export AWS_DEFAULT_REGION=us-west-2 

# Pass environment variables to cron jobs
printenv | grep -v "no_proxy" > /etc/environment

# Execute the main command (Flask app)
echo "Starting Flask application..."
exec "$@"
