#!/bin/bash

set -a
source .env
set +a


# docker build . --tag ${github_org}/dbaas:2.0.1 
docker build . --no-cache --tag ${github_org}/dbaas:2.0.1 
docker push ${github_org}/dbaas:2.0.1
