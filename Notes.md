# Testing Notes

### Backup Audit Notes

Backup Policy not set
  - dtenebatest
  - eprints-scholars
  - labsquirrel
  - nextcloud

All are listed in service list. All are MariaDB
All are created from restore.

Where is the meta data?
"labels": { "backup_feq": "Daily"}


Hmm, "backup_freq" is a TL of "Info"
Check the restore code from MairaDB - Compare with Postgres

policy = data["Info"].get("backup_freq")
