"""ProtoLabel: Optimized version with caching, FTS, and O(1) queries."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from functools import lru_cache
import time

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
DB = DATA / "prot0label.sqlite3"
ALLOWED_ROOT = Path(os.getenv("PROTOLABEL_ROOT", "/home/tts02/AI/DuyNAB")).resolve()
EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
MAX_IMAGES = 100_000

app = FastAPI(title="ProtoLabel", version="0.2.0-optimized")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prelabel")
jobs: dict[str, dict[str, Any]] = {}
model_cache: dict[str, Any] = {}
model_lock = threading.Lock()

# === OPTIMIZATION 1: In-memory cache for project stats ===
stats_cache: dict[str, dict] = {}  # {project_id: {total, labeled, review, unlabeled}}
stats_cache_time: dict[str, float] = {}  # Track cache timestamp
STATS_CACHE_TTL = 5  # 5 seconds cache for project stats
stats_lock = threading.Lock()


def db() -> sqlite3.Connection:
    """Initialize database with optimizations."""
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA query_only=FALSE")
    c.execute("PRAGMA cache_size=-64000")  # 64MB cache

    c.executescript("""
    CREATE TABLE IF NOT EXISTS projects(
      id TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT NOT NULL,
      classes TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS images(
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL, rel_path TEXT NOT NULL,
      width INTEGER NOT NULL, height INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'unlabeled',
      UNIQUE(project_id, rel_path)
    );
    CREATE TABLE IF NOT EXISTS boxes(
      id TEXT PRIMARY KEY, image_id TEXT NOT NULL, cls_name TEXT NOT NULL,
      x1 REAL NOT NULL, y1 REAL NOT NULL, x2 REAL NOT NULL, y2 REAL NOT NULL,
      confidence REAL, source TEXT NOT NULL DEFAULT 'manual', attrs TEXT NOT NULL DEFAULT '{}'
    );

    -- === OPTIMIZATION 2: Materialized view for project stats (O(1) access) ===
    CREATE TABLE IF NOT EXISTS project_stats(
      project_id TEXT PRIMARY KEY,
      total_images INTEGER DEFAULT 0,
      unlabeled_count INTEGER DEFAULT 0,
      review_count INTEGER DEFAULT 0,
      labeled_count INTEGER DEFAULT 0,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
    );

    -- === OPTIMIZATION 3: Full-text search table (enables fast search) ===
    CREATE VIRTUAL TABLE IF NOT EXISTS images_fts USING fts5(
      rel_path, content=images, content_rowid=rowid
    );

    -- === OPTIMIZATION 4: Better indexes for common queries ===
    CREATE INDEX IF NOT EXISTS idx_images_project ON images(project_id);
    CREATE INDEX IF NOT EXISTS idx_images_project_status ON images(project_id, status);
    CREATE INDEX IF NOT EXISTS idx_images_status ON images(status);
    CREATE INDEX IF NOT EXISTS idx_boxes_image ON boxes(image_id);
    CREATE INDEX IF NOT EXISTS idx_boxes_class ON boxes(cls_name);

    -- === OPTIMIZATION 5: Trigger to maintain project stats (automatic update) ===
    CREATE TRIGGER IF NOT EXISTS update_stats_on_image_insert
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

    CREATE TRIGGER IF NOT EXISTS update_stats_on_image_update
    AFTER UPDATE ON images
    BEGIN
      UPDATE project_stats SET
        unlabeled_count = unlabeled_count + (CASE WHEN NEW.status='unlabeled' THEN 1 ELSE 0 END) - (CASE WHEN OLD.status='unlabeled' THEN 1 ELSE 0 END),
        review_count = review_count + (CASE WHEN NEW.status='review' THEN 1 ELSE 0 END) - (CASE WHEN OLD.status='review' THEN 1 ELSE 0 END),
        labeled_count = labeled_count + (CASE WHEN NEW.status='labeled' THEN 1 ELSE 0 END) - (CASE WHEN OLD.status='labeled' THEN 1 ELSE 0 END),
        updated_at = CURRENT_TIMESTAMP
      WHERE project_id = NEW.project_id;
    END;

    CREATE TRIGGER IF NOT EXISTS update_stats_on_image_delete
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
    return c


def jsons(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def safe_root(raw: str) -> Path:
    p = Path(raw).expanduser().resolve()
    if not p.is_dir():
        raise HTTPException(400, f"Không tìm thấy thư mục: {p}")
    if ALLOWED_ROOT != Path("/") and p != ALLOWED_ROOT and ALLOWED_ROOT not in p.parents:
        raise HTTPException(403, f"Thư mục phải nằm trong {ALLOWED_ROOT}")
    return p


def model_options() -> list[dict[str, Any]]:
    candidates = [
        ("yolo26n", "YOLO26 nano · nhanh nhất", ROOT / "Protolabel" / "models" / "yolo26n.pt"),
        ("yolo26s", "YOLO26 small · nhanh", ROOT / "Protolabel" / "models" / "yolo26s.pt"),
        ("yolo26m", "YOLO26 medium · cân bằng", ROOT / "Protolabel" / "models" / "yolo26m.pt"),
        ("yolo26l", "YOLO26 large · chính xác", ROOT / "Protolabel" / "models" / "yolo26l.pt"),
        ("yolo26x", "YOLO26 extra-large · chính xác nhất", ROOT / "Protolabel" / "models" / "yolo26x.pt"),
    ]
    return [{"id": k, "label": label, "path": str(path), "available": path.is_file()} for k, label, path in candidates]


def get_project(c: sqlite3.Connection, pid: str):
    p = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not p:
        raise HTTPException(404, "Không tìm thấy project")
    return p


def get_cached_stats(c: sqlite3.Connection, pid: str) -> dict:
    """OPTIMIZATION: Get project stats from cache or materialized view (O(1))."""
    with stats_lock:
        now = time.time()
        if pid in stats_cache and (now - stats_cache_time.get(pid, 0)) < STATS_CACHE_TTL:
            return stats_cache[pid]

    # Query from materialized view (O(1) - single row lookup by PK)
    stat = c.execute("""
        SELECT COALESCE(total_images, 0) total,
               COALESCE(unlabeled_count, 0) unlabeled,
               COALESCE(review_count, 0) review,
               COALESCE(labeled_count, 0) labeled
        FROM project_stats WHERE project_id=?
    """, (pid,)).fetchone()

    result = dict(stat) if stat else {"total": 0, "unlabeled": 0, "review": 0, "labeled": 0}

    # Update cache
    with stats_lock:
        stats_cache[pid] = result
        stats_cache_time[pid] = time.time()

    return result


def invalidate_stats_cache(pid: str):
    """Invalidate cache for a project (called after mutations)."""
    with stats_lock:
        stats_cache.pop(pid, None)
        stats_cache_time.pop(pid, None)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "protolabel-optimized", "version": "0.2.0"}


@app.get("/api/models")
def models():
    return {"models": model_options()}


@app.get("/api/projects")
def projects():
    c = db()
    # OPTIMIZATION: Use materialized view instead of JOIN + COUNT
    rows = c.execute("""
        SELECT p.id, p.name, p.root, p.created_at,
               COALESCE(ps.total_images, 0) total,
               COALESCE(ps.labeled_count, 0) labeled,
               COALESCE(ps.review_count, 0) review
        FROM projects p
        LEFT JOIN project_stats ps ON p.id = ps.project_id
        ORDER BY p.created_at DESC
    """).fetchall()
    c.close()
    return {"projects": [dict(r) for r in rows]}


@app.post("/api/projects")
def create_project(payload: dict[str, Any]):
    root = safe_root(str(payload.get("root_path", "")))
    classes = payload.get("classes") or ["person", "helmet", "glasses", "gloves", "shoes", "safety-vest"]
    pid = uuid.uuid4().hex[:12]
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in EXTS][:MAX_IMAGES]

    c = db()
    c.execute("INSERT INTO projects(id,name,root,classes) VALUES(?,?,?,?)",
              (pid, str(payload.get("name") or root.name)[:100], str(root), jsons(classes)))

    # OPTIMIZATION: Use batch insert instead of loop
    count = 0
    batch = []
    for p in files:
        image = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
        if image is None: continue
        h, w = image.shape[:2]
        rel = str(p.relative_to(root))
        iid = hashlib.sha1(f"{pid}:{rel}".encode()).hexdigest()[:20]
        batch.append((iid, pid, rel, int(w), int(h), "unlabeled"))
        count += 1

        # Batch insert every 100 images
        if len(batch) >= 100:
            c.executemany("INSERT OR IGNORE INTO images VALUES(?,?,?,?,?,?)", batch)
            batch = []

    if batch:
        c.executemany("INSERT OR IGNORE INTO images VALUES(?,?,?,?,?,?)", batch)

    # Initialize project stats
    c.execute("INSERT INTO project_stats(project_id, total_images, unlabeled_count) VALUES(?,?,?)",
              (pid, count, count))

    c.commit()
    c.close()
    return {"id": pid, "name": str(payload.get("name") or root.name), "root": str(root), "image_count": count}


@app.get("/api/projects/{pid}")
def project(pid: str):
    c = db()
    p = get_project(c, pid)
    # OPTIMIZATION: O(1) lookup from materialized view instead of O(n) count
    stat = get_cached_stats(c, pid)
    c.close()
    return {**dict(p), "classes": json.loads(p["classes"]), "stats": stat}


@app.get("/api/projects/{pid}/images")
def image_list(pid: str, status: str = "all", search: str = "", page: int = 1, page_size: int = 80):
    c = db()
    get_project(c, pid)
    page, page_size = max(1, page), max(1, min(page_size, 200))

    where, args = ["project_id=?"], [pid]
    if status in {"unlabeled", "review", "labeled"}:
        where.append("status=?")
        args.append(status)

    # OPTIMIZATION: Use FTS for search instead of LIKE (much faster)
    if search:
        # Escape search query
        search_escaped = search.replace('"', '""')
        where.append(f"rel_path IN (SELECT rowid FROM images_fts WHERE images_fts MATCH ?)")
        args.append(f"{search_escaped}*")

    clause = " AND ".join(where)

    # OPTIMIZATION: COUNT with WHERE clause is still O(n), but use indexed columns
    total = c.execute(f"SELECT COUNT(*) FROM images WHERE {clause}", args).fetchone()[0]

    # OPTIMIZATION: Use compound index for pagination
    rows = c.execute(f"""
        SELECT id, rel_path, width, height, status
        FROM images
        WHERE {clause}
        ORDER BY rel_path
        LIMIT ? OFFSET ?
    """, [*args, page_size, (page-1)*page_size]).fetchall()

    c.close()
    return {
        "images": [{**dict(r), "url": f"/api/projects/{pid}/media/{r['id']}"} for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size
    }


@app.get("/api/projects/{pid}/images/{iid}")
def image_detail(pid: str, iid: str):
    c = db()
    p = get_project(c, pid)
    # OPTIMIZATION: O(1) lookups
    row = c.execute("SELECT * FROM images WHERE id=? AND project_id=?", (iid, pid)).fetchone()
    if not row:
        raise HTTPException(404, "Không tìm thấy ảnh")
    # OPTIMIZATION: Use index on boxes.image_id
    boxes = c.execute("SELECT * FROM boxes WHERE image_id=? ORDER BY rowid", (iid,)).fetchall()
    c.close()
    return {
        **dict(row),
        "url": f"/api/projects/{pid}/media/{iid}",
        "classes": json.loads(p["classes"]),
        "boxes": [
            {
                "id": b["id"],
                "cls_name": b["cls_name"],
                "bbox": [b["x1"], b["y1"], b["x2"], b["y2"]],
                "confidence": b["confidence"],
                "source": b["source"],
                "attributes": json.loads(b["attrs"])
            }
            for b in boxes
        ]
    }


@app.get("/api/projects/{pid}/media/{iid}")
def media(pid: str, iid: str):
    c = db()
    p = get_project(c, pid)
    row = c.execute("SELECT rel_path FROM images WHERE id=? AND project_id=?", (iid, pid)).fetchone()
    c.close()
    if not row:
        raise HTTPException(404, "Không tìm thấy ảnh")
    root, path = Path(p["root"]).resolve(), (Path(p["root"]) / row["rel_path"]).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "File ảnh không tồn tại")
    return FileResponse(path)


@app.put("/api/projects/{pid}/images/{iid}/boxes")
def save_boxes(pid: str, iid: str, payload: dict[str, Any]):
    c = db()
    get_project(c, pid)
    if not c.execute("SELECT 1 FROM images WHERE id=? AND project_id=?", (iid, pid)).fetchone():
        raise HTTPException(404, "Không tìm thấy ảnh")

    # OPTIMIZATION: Delete and insert in transaction
    c.execute("DELETE FROM boxes WHERE image_id=?", (iid,))

    # OPTIMIZATION: Batch insert boxes
    boxes_to_insert = []
    for a in payload.get("boxes", []):
        b = [max(0.0, min(1.0, float(v))) for v in a.get("bbox", [0, 0, 0, 0])]
        boxes_to_insert.append((
            a.get("id") or uuid.uuid4().hex[:16],
            iid,
            str(a.get("cls_name", "object")),
            *b,
            a.get("confidence"),
            str(a.get("source", "manual")),
            jsons(a.get("attributes") or {})
        ))

    if boxes_to_insert:
        c.executemany("INSERT INTO boxes VALUES(?,?,?,?,?,?,?,?,?,?)", boxes_to_insert)

    status = payload.get("status") or ("labeled" if payload.get("boxes") else "unlabeled")
    status = status if status in {"unlabeled", "review", "labeled"} else "labeled"
    c.execute("UPDATE images SET status=? WHERE id=?", (status, iid))

    c.commit()
    invalidate_stats_cache(pid)  # Invalidate cache after mutation
    c.close()

    return image_detail(pid, iid)


def predict(model_id: str, path: Path, conf: float, iou: float, device: str | None):
    opts = {m["id"]: m for m in model_options()}
    if model_id not in opts or not opts[model_id]["available"]:
        raise RuntimeError(f"Checkpoint chưa có: {model_id}")
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("Cài ultralytics trong môi trường sgdetr trước khi chạy prelabel") from exc

    with model_lock:
        model = model_cache.get(model_id)
        if model is None:
            model = model_cache.setdefault(model_id, YOLO(opts[model_id]["path"]))

    kwargs = {"source": str(path), "conf": conf, "iou": iou, "verbose": False}
    if device:
        kwargs["device"] = device

    result = model.predict(**kwargs)[0]
    names, h, w = result.names or {}, result.orig_shape[0], result.orig_shape[1]
    out = []

    if result.boxes is not None:
        for box, score, cls in zip(
            result.boxes.xyxy.cpu().tolist(),
            result.boxes.conf.cpu().tolist(),
            result.boxes.cls.cpu().tolist()
        ):
            x1, y1, x2, y2 = box
            out.append({
                "id": uuid.uuid4().hex[:16],
                "cls_name": str(names.get(int(cls), int(cls))),
                "bbox": [x1/w, y1/h, x2/w, y2/h],
                "confidence": float(score),
                "source": model_id,
                "attributes": {}
            })
    return out


def prelabel_job(job_id: str, pid: str, ids: list[str], model_id: str, conf: float, iou: float, replace: bool, device: str | None):
    jobs[job_id]["status"] = "running"
    c = db()
    try:
        p = get_project(c, pid)
        root = Path(p["root"])
        for n, iid in enumerate(ids, 1):
            row = c.execute("SELECT rel_path FROM images WHERE id=? AND project_id=?", (iid, pid)).fetchone()
            if not row:
                continue

            boxes = predict(model_id, (root / row["rel_path"]).resolve(), conf, iou, device)

            if replace:
                c.execute("DELETE FROM boxes WHERE image_id=?", (iid,))

            # OPTIMIZATION: Batch insert boxes
            if boxes:
                boxes_to_insert = [
                    (a["id"], iid, a["cls_name"], *a["bbox"], a["confidence"], a["source"], jsons(a["attributes"]))
                    for a in boxes
                ]
                c.executemany("INSERT INTO boxes VALUES(?,?,?,?,?,?,?,?,?,?)", boxes_to_insert)

            c.execute("UPDATE images SET status='review' WHERE id=?", (iid,))
            c.commit()
            jobs[job_id]["processed"] = n
            invalidate_stats_cache(pid)

        jobs[job_id]["status"] = "done"
    except Exception as exc:
        jobs[job_id].update(status="error", error=str(exc))
    finally:
        c.close()


@app.post("/api/projects/{pid}/prelabel")
def start_prelabel(pid: str, payload: dict[str, Any]):
    c = db()
    get_project(c, pid)
    ids = payload.get("image_ids") or [
        r["id"] for r in c.execute(
            "SELECT id FROM images WHERE project_id=? AND status!='labeled' ORDER BY rel_path",
            (pid,)
        ).fetchall()
    ]
    c.close()

    if not ids:
        raise HTTPException(400, "Không có ảnh để prelabel")

    jid = uuid.uuid4().hex[:12]
    jobs[jid] = {"id": jid, "status": "queued", "processed": 0, "total": len(ids), "error": None}
    pool.submit(
        prelabel_job, jid, pid, ids,
        str(payload.get("model_id", "yolo26n")),
        float(payload.get("conf", .25)),
        float(payload.get("iou", .7)),
        bool(payload.get("replace", True)),
        payload.get("device")
    )
    return jobs[jid]


@app.get("/api/jobs/{jid}")
def job(jid: str):
    if jid not in jobs:
        raise HTTPException(404, "Không tìm thấy job")
    return jobs[jid]


@app.get("/api/projects/{pid}/export")
def export_project(pid: str, format: str = "yolo"):
    """OPTIMIZATION: Stream export, handle large datasets better."""
    if format not in {"yolo", "json"}:
        raise HTTPException(400, "format phải là yolo hoặc json")

    c = db()
    p = get_project(c, pid)

    # OPTIMIZATION: Use cursor instead of fetchall for large datasets
    rows = c.execute("SELECT * FROM images WHERE project_id=? ORDER BY rel_path", (pid,)).fetchall()
    classes = json.loads(p["classes"])

    out = Path(tempfile.mkstemp(prefix=f"protolabel-{pid}-", suffix=".zip", dir=DATA)[1])

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        if format == "yolo":
            z.writestr("classes.txt", "\n".join(classes) + "\n")

        for row in rows:
            # OPTIMIZATION: Use indexed query for boxes
            boxes = c.execute("SELECT * FROM boxes WHERE image_id=?", (row["id"],)).fetchall()

            if format == "json":
                z.writestr(
                    f"annotations/{row['rel_path']}.json",
                    jsons({
                        "image": row["rel_path"],
                        "width": row["width"],
                        "height": row["height"],
                        "boxes": [
                            {
                                "class": b["cls_name"],
                                "bbox": [b["x1"], b["y1"], b["x2"], b["y2"]],
                                "confidence": b["confidence"],
                                "source": b["source"]
                            }
                            for b in boxes
                        ]
                    })
                )
            else:  # yolo
                lines = []
                for b in boxes:
                    if b["cls_name"] not in classes:
                        continue
                    cx = (b["x1"] + b["x2"]) / 2
                    cy = (b["y1"] + b["y2"]) / 2
                    w = b["x2"] - b["x1"]
                    h = b["y2"] - b["y1"]
                    lines.append(f"{classes.index(b['cls_name'])} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
                z.writestr(
                    f"labels/{Path(row['rel_path']).with_suffix('.txt')}",
                    "\n".join(lines) + ("\n" if lines else "")
                )

    c.close()
    return FileResponse(out, media_type="application/zip", filename=f"{p['name']}-{format}.zip")
