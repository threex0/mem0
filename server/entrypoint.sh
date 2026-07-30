#!/bin/sh
set -e

# Force creation of directories and permissions at container startup
mkdir -p /tmp/.mem0 /root/.mem0
touch /tmp/.mem0/history.db /root/.mem0/history.db
chmod -R 777 /tmp /root/.mem0

# Execute whatever CMD is passed (uvicorn)
exec "$@"
