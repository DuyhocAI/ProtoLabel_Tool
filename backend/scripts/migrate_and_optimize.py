#!/usr/bin/env python3
"""
Migration script to upgrade ProtoLabel from original to optimized version.
This script:
1. Creates new tables (project_stats, images_fts)
2. Creates new indexes and triggers
3. Populates stats from existing images
4. Rebuilds FTS index

Usage: python migrate_and_optimize.py
"""
import os
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(os.getenv("PROTOLABEL_DB_PATH", Path(__file__).resolve().parents[3] / "data" / "prot0label.sqlite3")).resolve()

def migrate():
    if not DB_PATH.exists():
        print(f"❌ Database not found: {DB_PATH}")
        return False

    print(f"📁 Connecting to: {DB_PATH}")
    c = sqlite3.connect(str(DB_PATH), timeout=30)
    c.row_factory = sqlite3.Row

    try:
        # Step 1: Create new tables
        print("\n1️⃣  Creating new tables...")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS project_stats(
          project_id TEXT PRIMARY KEY,
          total_images INTEGER DEFAULT 0,
          unlabeled_count INTEGER DEFAULT 0,
          review_count INTEGER DEFAULT 0,
          labeled_count INTEGER DEFAULT 0,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS images_fts USING fts5(
          rel_path, content=images, content_rowid=rowid
        );
        """)
        print("   ✅ Tables created")

        # Step 2: Create new indexes
        print("\n2️⃣  Creating optimized indexes...")
        c.executescript("""
        DROP INDEX IF EXISTS image_project_status;

        CREATE INDEX IF NOT EXISTS idx_images_project ON images(project_id);
        CREATE INDEX IF NOT EXISTS idx_images_project_status ON images(project_id, status);
        CREATE INDEX IF NOT EXISTS idx_images_status ON images(status);
        CREATE INDEX IF NOT EXISTS idx_boxes_image ON boxes(image_id);
        CREATE INDEX IF NOT EXISTS idx_boxes_class ON boxes(cls_name);
        """)
        print("   ✅ Indexes created")

        # Step 3: Create triggers
        print("\n3️⃣  Creating triggers for auto-sync...")
        c.executescript("""
        DROP TRIGGER IF EXISTS update_stats_on_image_insert;
        DROP TRIGGER IF EXISTS update_stats_on_image_update;
        DROP TRIGGER IF EXISTS update_stats_on_image_delete;

        CREATE TRIGGER update_stats_on_image_insert
        AFTER INSERT ON images
        BEGIN
          INSERT INTO project_stats(project_id, total_images, unlabeled_count, review_count, labeled_count)
          VALUES(NEW.project_id, 1, CASE WHEN NEW.status='unlabeled' THEN 1 ELSE 0 END,
                 CASE WHEN NEW.status='review' THEN 1 ELSE 0 END,
                 CASE WHEN NEW.status='labeled' THEN 1 ELSE 0 END)
          ON CONFLICT(project_id) DO UPDATE SET
            total_images = total_images + 1,
            unlabeled_count = CASE WHEN NEW.status='unlabeled' THEN unlabeled_count + 1 ELSE unlabeled_count END,
            review_count = CASE WHEN NEW.status='review' THEN review_count + 1 ELSE review_count END,
            labeled_count = CASE WHEN NEW.status='labeled' THEN labeled_count + 1 ELSE labeled_count END,
            updated_at = CURRENT_TIMESTAMP;
        END;

        CREATE TRIGGER update_stats_on_image_update
        AFTER UPDATE ON images
        BEGIN
          UPDATE project_stats SET
            unlabeled_count = unlabeled_count + (CASE WHEN NEW.status='unlabeled' THEN 1 ELSE 0 END) - (CASE WHEN OLD.status='unlabeled' THEN 1 ELSE 0 END),
            review_count = review_count + (CASE WHEN NEW.status='review' THEN 1 ELSE 0 END) - (CASE WHEN OLD.status='review' THEN 1 ELSE 0 END),
            labeled_count = labeled_count + (CASE WHEN NEW.status='labeled' THEN 1 ELSE 0 END) - (CASE WHEN OLD.status='labeled' THEN 1 ELSE 0 END),
            updated_at = CURRENT_TIMESTAMP
          WHERE project_id = NEW.project_id;
        END;

        CREATE TRIGGER update_stats_on_image_delete
        AFTER DELETE ON images
        BEGIN
          UPDATE project_stats SET
            total_images = total_images - 1,
            unlabeled_count = CASE WHEN OLD.status='unlabeled' THEN unlabeled_count - 1 ELSE unlabeled_count END,
            review_count = CASE WHEN OLD.status='review' THEN review_count - 1 ELSE review_count END,
            labeled_count = CASE WHEN OLD.status='labeled' THEN labeled_count - 1 ELSE labeled_count END,
            updated_at = CURRENT_TIMESTAMP
          WHERE project_id = OLD.project_id;
        END;
        """)
        print("   ✅ Triggers created")

        # Step 4: Populate stats from existing images
        print("\n4️⃣  Populating project stats...")
        projects = c.execute("SELECT id FROM projects").fetchall()
        for (pid,) in projects:
            total = c.execute("SELECT COUNT(*) FROM images WHERE project_id=?", (pid,)).fetchone()[0]
            unlabeled = c.execute("SELECT COUNT(*) FROM images WHERE project_id=? AND status='unlabeled'", (pid,)).fetchone()[0]
            review = c.execute("SELECT COUNT(*) FROM images WHERE project_id=? AND status='review'", (pid,)).fetchone()[0]
            labeled = c.execute("SELECT COUNT(*) FROM images WHERE project_id=? AND status='labeled'", (pid,)).fetchone()[0]

            c.execute("""
                INSERT OR REPLACE INTO project_stats
                (project_id, total_images, unlabeled_count, review_count, labeled_count)
                VALUES(?, ?, ?, ?, ?)
            """, (pid, total, unlabeled, review, labeled))
            print(f"   ✅ {pid}: {total} images ({labeled} labeled, {review} review)")

        # Step 5: Rebuild FTS index
        print("\n5️⃣  Building full-text search index...")
        c.execute("DELETE FROM images_fts")
        rows = c.execute("SELECT rowid, rel_path FROM images").fetchall()
        batch = [(r[0], r[1]) for r in rows]
        c.executemany("INSERT INTO images_fts(rowid, rel_path) VALUES(?, ?)", batch)
        print(f"   ✅ FTS index built for {len(batch)} images")

        # Step 6: Optimize database
        print("\n6️⃣  Optimizing database...")
        c.commit()
        c.execute("VACUUM")
        c.execute("ANALYZE")
        print("   ✅ Database optimized")

        c.commit()
        print("\n✅ Migration complete! Your database is now optimized.")
        print("\nNext steps:")
        print("1. Restart the backend server")
        print("2. Run the benchmark to verify indexes")
        print("3. Keep a verified database backup")

        return True

    except Exception as e:
        print(f"\n❌ Error: {e}")
        c.rollback()
        return False
    finally:
        c.close()

if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
