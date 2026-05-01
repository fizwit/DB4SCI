# General Notes

### Successful start of admin DBs
```
./deploy_admin.sh
no such service: mydb_admin_db
Starting admin_db
Creating service mydb_admin_db
overall progress: 1 out of 1 tasks
1/1: running   [==================================================>]
verify: Service hyqja6hls6uvrw3pv9cvrhmgi converged
no such service: mydb_migrate_db
Starting migrate_db
Creating service mydb_migrate_db
overall progress: 1 out of 1 tasks
1/1: running   [==================================================>]
verify: Service 3wabx0h7bmxmfr9aoz1xcd79e converged
```

### How can I preview one of the docker YAML files with variable interpolation?

```
source .env
docker-compose --file migrate_db.yml config
```

