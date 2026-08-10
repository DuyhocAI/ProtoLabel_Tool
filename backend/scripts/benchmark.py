#!/usr/bin/env python3
"""Benchmark the SQLite queries used by the current ProtoLabel backend."""
import os
import sqlite3
import statistics
import time
from pathlib import Path

_default_data = Path(os.getenv("PROTOLABEL_DATA_DIR", Path(__file__).resolve().parents[3] / "data"))
DB_PATH = Path(os.getenv("PROTOLABEL_DB_PATH", _default_data / "prot0label.sqlite3")).resolve()


def measure(connection, sql, args=(), repeats=20):
    samples = []
    for _ in range(repeats):
        started = time.perf_counter(); connection.execute(sql, args).fetchall()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples)


def main() -> int:
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}"); return 1
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type=\"index\"")}
    required = {"image_project_status", "box_image", "user_events_user_time", "user_events_project_time", "sessions_user"}
    print(f"Database: {DB_PATH}")
    print("Missing runtime indexes:", sorted(required-indexes) or "none")
    project = connection.execute("SELECT id FROM projects LIMIT 1").fetchone()
    if project:
        pid = project[0]
        print("Project image page: %.3f ms" % measure(connection, "SELECT id,rel_path,width,height,status FROM images WHERE project_id=? ORDER BY rel_path LIMIT 80", (pid,)))
        print("Project status count: %.3f ms" % measure(connection, "SELECT COUNT(*) FROM images WHERE project_id=? AND status=?", (pid, "labeled")))
        print("Box lookup: %.3f ms" % measure(connection, "SELECT * FROM boxes WHERE image_id=(SELECT id FROM images WHERE project_id=? LIMIT 1)", (pid,)))
    print("Performance events: %.3f ms" % measure(connection, "SELECT user_id,COUNT(*) FROM user_events GROUP BY user_id"))
    connection.close(); return 0


if __name__ == "__main__":
    raise SystemExit(main())
