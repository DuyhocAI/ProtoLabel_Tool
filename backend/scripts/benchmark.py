#!/usr/bin/env python3
"""
Benchmark script to measure optimization impact.
Compares original vs optimized database performance.

Usage: python benchmark.py
"""
import os
import sqlite3
import time
import json
from pathlib import Path

DB_PATH = Path(os.getenv("PROTOLABEL_DB_PATH", Path(__file__).resolve().parents[3] / "data" / "prot0label.sqlite3")).resolve()

def time_query(c, query, params=None, label=""):
    """Execute query and measure time."""
    start = time.time()
    try:
        if params:
            result = c.execute(query, params).fetchall()
        else:
            result = c.execute(query).fetchall()
        elapsed = (time.time() - start) * 1000  # ms
        status = "✅"
    except Exception as e:
        elapsed = -1
        status = "❌"
        result = str(e)

    print(f"{status} {label:40} {elapsed:8.2f}ms" + (f" ({len(result)} rows)" if isinstance(result, list) else ""))
    return elapsed

def benchmark():
    if not DB_PATH.exists():
        print(f"❌ Database not found: {DB_PATH}")
        return

    c = sqlite3.connect(str(DB_PATH), timeout=30)
    c.row_factory = sqlite3.Row

    print("=" * 70)
    print("ProtoLabel Database Benchmark")
    print("=" * 70)

    # Get stats
    projects_count = c.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    images_count = c.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    boxes_count = c.execute("SELECT COUNT(*) FROM boxes").fetchone()[0]

    print(f"\n📊 Database Stats:")
    print(f"   Projects: {projects_count}")
    print(f"   Images: {images_count:,}")
    print(f"   Boxes: {boxes_count:,}")

    # Sample project
    sample_project = c.execute("SELECT id FROM projects LIMIT 1").fetchone()
    if not sample_project:
        print("❌ No projects found. Create a project first.")
        c.close()
        return

    pid = sample_project["id"]
    print(f"\n📁 Testing with project: {pid}")

    print("\n" + "=" * 70)
    print("BENCHMARK RESULTS")
    print("=" * 70)

    # Test 1: Project list (old vs new)
    print("\n1. GET ALL PROJECTS")
    print("-" * 70)
    if projects_count > 0:
        time_query(c, """
            SELECT p.id, p.name, p.root, COUNT(i.id) total
            FROM projects p LEFT JOIN images i ON p.id=i.project_id
            GROUP BY p.id
        """, label="Old method (JOIN + GROUP BY)")

    # Check if optimized version available
    stats_exists = c.execute("""
        SELECT name FROM sqlite_master WHERE type='table' AND name='project_stats'
    """).fetchone()

    if stats_exists:
        time_query(c, """
            SELECT p.id, p.name, p.root, COALESCE(ps.total_images, 0) total
            FROM projects p LEFT JOIN project_stats ps ON p.id=ps.project_id
        """, label="New method (materialized view)")

    # Test 2: Project details
    print("\n2. GET PROJECT DETAILS (stats)")
    print("-" * 70)
    time_query(c, """
        SELECT COUNT(*) total, SUM(status='unlabeled') unlabeled,
               SUM(status='review') review, SUM(status='labeled') labeled
        FROM images WHERE project_id=?
    """, (pid,), label="Old method (SUM aggregates)")

    if stats_exists:
        time_query(c, """
            SELECT total_images, unlabeled_count, review_count, labeled_count
            FROM project_stats WHERE project_id=?
        """, (pid,), label="New method (direct lookup)")

    # Test 3: Image search
    print("\n3. SEARCH BY FILENAME")
    print("-" * 70)
    search_term = "frame%"
    time_query(c, """
        SELECT COUNT(*) FROM images WHERE project_id=? AND rel_path LIKE ?
    """, (pid, search_term), label="Old method (LIKE scan)")

    fts_exists = c.execute("""
        SELECT name FROM sqlite_master WHERE type='table' AND name='images_fts'
    """).fetchone()

    if fts_exists:
        time_query(c, """
            SELECT COUNT(*) FROM images WHERE rel_path IN (
                SELECT rowid FROM images_fts WHERE images_fts MATCH ?
            )
        """, (f"{search_term.strip('%')}*",), label="New method (FTS5)")

    # Test 4: Get image list with pagination
    print("\n4. GET IMAGE LIST (pagination)")
    print("-" * 70)
    time_query(c, """
        SELECT id, rel_path FROM images WHERE project_id=?
        ORDER BY rel_path LIMIT 80 OFFSET 0
    """, (pid,), label="Single page fetch (80 images)")

    # Test 5: Get boxes for multiple images
    print("\n5. GET ANNOTATIONS (boxes)")
    print("-" * 70)
    sample_images = c.execute(
        "SELECT id FROM images WHERE project_id=? LIMIT 5", (pid,)
    ).fetchall()

    if sample_images:
        image_id = sample_images[0]["id"]
        time_query(c, """
            SELECT * FROM boxes WHERE image_id=?
        """, (image_id,), label="Get boxes for 1 image (indexed)")

    # Test 6: Check indexes
    print("\n6. INDEX STATISTICS")
    print("-" * 70)
    indexes = c.execute("""
        SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='images'
    """).fetchall()
    print(f"   Indexes on 'images' table:")
    for idx in indexes:
        print(f"   - {idx['name']}")

    indexes_boxes = c.execute("""
        SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='boxes'
    """).fetchall()
    print(f"   Indexes on 'boxes' table:")
    for idx in indexes_boxes:
        print(f"   - {idx['name']}")

    # Test 7: Database size
    print("\n7. DATABASE INFORMATION")
    print("-" * 70)
    db_size = Path(DB_PATH).stat().st_size / 1024 / 1024
    wal_size = 0
    wal_path = Path(f"{DB_PATH}-wal")
    if wal_path.exists():
        wal_size = wal_path.stat().st_size / 1024 / 1024

    print(f"   Main DB file: {db_size:.2f} MB")
    if wal_size > 0:
        print(f"   WAL file: {wal_size:.2f} MB")

    # Table sizes
    print(f"\n   Table statistics:")
    for table in ["projects", "images", "boxes"]:
        count = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        size = c.execute(f"SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size() WHERE name='{table}'").fetchone()
        print(f"   - {table:15} {count:>10,} rows")

    print("\n" + "=" * 70)
    print("RECOMMENDATIONS")
    print("=" * 70)

    recommendations = []

    if images_count > 10000:
        recommendations.append("✅ Large dataset detected. Optimizations will help significantly!")

    if stats_exists:
        recommendations.append("✅ Materialized view 'project_stats' is present")
    else:
        recommendations.append("❌ Run migration script to add 'project_stats' table")

    if fts_exists:
        recommendations.append("✅ Full-text search 'images_fts' is present")
    else:
        recommendations.append("❌ Run migration script to add FTS index")

    if db_size > 1000:
        recommendations.append(f"💡 Database is large ({db_size:.0f}MB). Consider archiving old projects")

    for rec in recommendations:
        print(f"  {rec}")

    print("\n" + "=" * 70)

    c.close()

if __name__ == "__main__":
    benchmark()
