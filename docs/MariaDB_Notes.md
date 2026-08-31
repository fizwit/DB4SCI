# MariaDB Notes

### File Key Management

File Key Management works with 
The File Key Management plugin is included in MariaDB packages as the file_key_management.so
but is not part of the image by default. It has to be enabled via command line or with an 
options file.

```
[mariadb]
plugin_load_add = file_key_management
file_key_management_filename = /etc/mysql/encryption/keyfile.txt
# The following line is optional but highly recommended.
# Uncomment it to enable usage of an encrypted key file.
# file_key_management_filekey = FILE:/etc/mysql/encryption/keyfile.key
file_key_management_encryption_algorithm = AES_CTR
``

#### Enable Encryption with config file

```
/etc/my.cnf
[mariadb]
innodb_encrypt_tables = ON
innodb_encrypt_log = ON
innodb_encrypt_temporary_tables = ON
aria_encrypt_tables = ON
encrypt_tmp_disk_tables = ON
```


### Which Plugins Does the Container Contain?
```
docker run --rm mariadb:12.0.2  ls -C /usr/lib/mysql/plugin
auth_ed25519.so         ha_blackhole.so          query_cache_info.so
auth_pam.so             ha_federated.so          query_response_time.so
auth_pam_tool_dir       ha_federatedx.so         server_audit.so
auth_pam_v1.so          ha_sphinx.so             simple_password_check.so
auth_parsec.so          handlersocket.so         sql_errlog.so
disks.so                locales.so               type_mysql_json.so
file_key_management.so  metadata_lock_info.so    wsrep_info.so
ha_archive.so           password_reuse_check.so
```

## Example of Enrypted MariaDB

Encryption key is in seperate Bind Mount. But password for Key is send via Docker environment variable.
Not absoultly secure, but meets minime requiment for encryption at rest.
[encryptedMariaDBDocker](https://github.com/alibell/encryptedMariaDBDocker/blob/main/runMariaDB.sh)


```
# Launch mariadb with encrryption on command line
/usr/local/bin/docker-entrypoint.sh \
    --plugin-load-add=file_key_management \
    --file-key-management-filekey=$PASSWORD \
    --file-key-management-filename=$KEY_PATH \
    --innodb-encrypt-tables=1 \
    --innodb-encrypt-temporary-tables=1 \
    --innodb-encrypt-log=1 \
    --innodb-encryption-threads=4 \
    --innodb-encryption-rotate-key-age=1
```
