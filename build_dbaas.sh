#!/bin/bash

source .env
docker build . --no-cache --tag ${github_org}/dbaas:2.0.1 
docker push ${github_org}/dbaas:2.0.1
