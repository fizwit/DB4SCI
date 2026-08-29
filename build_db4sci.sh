#!/bin/bash

set -a
source .env
set +a


# docker build . --tag ${org_repo_name}/db4sci:2.0.1 
docker build . --no-cache --tag ${org_repo_name}/db4sci:2.0.1 
docker push ${org_repo_name}/db4sci:2.0.1
