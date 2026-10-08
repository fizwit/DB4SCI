#!/bin/bash
set -e

# Start cron in the background
echo "Starting cron daemon..."
cron

# Pass environment variables to cron jobs
printenv | grep -v "no_proxy" > /etc/environment

# Execute the main command (Flask app)
echo "Starting DB4Sci application..."
exec "$@"
