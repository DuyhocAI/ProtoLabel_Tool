# Database maintenance

`migrate_and_optimize.py` now only aligns an existing ProtoLabel database with indexes used by the current backend. It also removes legacy `project_stats`, `images_fts`, and their synchronization triggers because the runtime does not query them.

Stop the backend and keep a verified database backup before running:

```bash
cd /path/to/Protolabel
python backend/scripts/migrate_and_optimize.py
```

By default the script uses `$PROTOLABEL_DATA_DIR/prot0label.sqlite3`, or the workspace-level `data/prot0label.sqlite3`. Override the exact file when needed:

```bash
PROTOLABEL_DB_PATH=/absolute/path/prot0label.sqlite3 python backend/scripts/migrate_and_optimize.py
```

The operation is idempotent. It removes only the obsolete optimization tables/triggers, recreates current runtime indexes, then runs `ANALYZE` and `VACUUM`.
