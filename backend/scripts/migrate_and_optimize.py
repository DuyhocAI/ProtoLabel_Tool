#!/usr/bin/env python3
"""Remove obsolete optimization artifacts and align indexes with the current runtime."""
import os
import sqlite3
import sys
from pathlib import Path

_default_data = Path(os.getenv("PROTOLABEL_DATA_DIR", Path(__file__).resolve().parents[3] / "data"))
DB_PATH = Path(os.getenv("PROTOLABEL_DB_PATH", _default_data / "prot0label.sqlite3")).resolve()


def migrate() -> bool:
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        return False
    print(f"Connecting to: {DB_PATH}")
    connection = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        connection.executescript("""
        DROP TRIGGER IF EXISTS update_stats_on_image_insert;
        DROP TRIGGER IF EXISTS update_stats_on_image_update;
        DROP TRIGGER IF EXISTS update_stats_on_image_delete;
        DROP TABLE IF EXISTS project_stats;
        DROP TABLE IF EXISTS images_fts;
        CREATE INDEX IF NOT EXISTS image_project_status ON images(project_id,status,rel_path);
        CREATE INDEX IF NOT EXISTS box_image ON boxes(image_id);
        CREATE INDEX IF NOT EXISTS user_events_user_time ON user_events(user_id,created_at);
        CREATE INDEX IF NOT EXISTS user_events_project_time ON user_events(project_id,created_at);
        CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
        """)
        connection.commit()
        connection.execute("ANALYZE")
        connection.execute("VACUUM")
        print("Migration complete: obsolete stats/FTS artifacts removed; runtime indexes verified.")
        return True
    except Exception as exc:
        connection.rollback()
        print(f"Migration failed: {exc}")
        return False
    finally:
        connection.close()


if __name__ == "__main__":
    sys.exit(0 if migrate() else 1)
