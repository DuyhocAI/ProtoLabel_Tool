"""ProtoLabel: local-first dataset labeling and model-assisted review API."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import cv2
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse

from .auth import authenticate, init_auth_schema, record_event, record_image_open, router as auth_router

_workspace_root = os.getenv("PROTOLABEL_WORKSPACE_ROOT")
ROOT = Path(_workspace_root).resolve() if _workspace_root else Path(__file__).resolve().parents[3]
DATA = Path(os.getenv("PROTOLABEL_DATA_DIR", ROOT / "data")).resolve()
MODEL_DIR = Path(os.getenv("PROTOLABEL_MODEL_DIR", ROOT / "Protolabel" / "models")).resolve()
DB = DATA / "prot0label.sqlite3"
ALLOWED_ROOT = Path(os.getenv("PROTOLABEL_ROOT", ROOT)).resolve()
EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
MAX_UPLOAD_BYTES = int(os.getenv("PROTOLABEL_MAX_UPLOAD_BYTES", str(2 * 1024**3)))
MAX_UPLOAD_FILES = int(os.getenv("PROTOLABEL_MAX_UPLOAD_FILES", "10000"))
JOB_RETENTION_SECONDS = int(os.getenv("PROTOLABEL_JOB_RETENTION_SECONDS", "86400"))
JOB_DIR = DATA / "jobs"
logger = logging.getLogger("protolabel")

app = FastAPI(title="ProtoLabel", version="0.2.0")
app.include_router(auth_router)
# CORS restricted to frontend origin only (security fix)
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:8101,http://127.0.0.1:8101").split(",")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type", "Authorization"])
pool = ThreadPoolExecutor(max_workers=max(2, int(os.getenv("PROTOLABEL_JOB_WORKERS", "2"))), thread_name_prefix="protolabel-job")
jobs: dict[str, dict[str, Any]] = {}
jobs_lock = threading.Lock()
job_last_persisted: dict[str, float] = {}
model_cache: dict[str, Any] = {}
model_lock = threading.Lock()


@app.middleware("http")
async def require_auth(request: Request, call_next):
    return await authenticate(request, call_next)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    logger.exception("Unhandled request error on %s", request.url.path, exc_info=exc)
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


def init_db() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript("""
    CREATE TABLE IF NOT EXISTS projects(
      id TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT NOT NULL,
      classes TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS images(
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL, rel_path TEXT NOT NULL,
      width INTEGER NOT NULL, height INTEGER NOT NULL, status TEXT NOT NULL DEFAULT "unlabeled",
      UNIQUE(project_id, rel_path)
    );
    CREATE TABLE IF NOT EXISTS boxes(
      id TEXT PRIMARY KEY, image_id TEXT NOT NULL, cls_name TEXT NOT NULL,
      x1 REAL NOT NULL, y1 REAL NOT NULL, x2 REAL NOT NULL, y2 REAL NOT NULL,
      confidence REAL, source TEXT NOT NULL DEFAULT "manual", attrs TEXT NOT NULL DEFAULT "{}"
    );
    CREATE INDEX IF NOT EXISTS image_project_status ON images(project_id,status,rel_path);
    CREATE INDEX IF NOT EXISTS box_image ON boxes(image_id);
    CREATE TABLE IF NOT EXISTS app_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        convention = c.execute("SELECT value FROM app_metadata WHERE key=?", ("bbox_coordinate_system",)).fetchone()
        if convention is None:
            box_count = c.execute("SELECT COUNT(*) FROM boxes").fetchone()[0]
            if box_count:
                raise RuntimeError(
                    "Database has boxes but no bbox_coordinate_system marker. "
                    "Refusing to mutate coordinates automatically; restore metadata or run an explicit migration."
                )
            c.execute("INSERT INTO app_metadata(key,value) VALUES(?,?)", ("bbox_coordinate_system", "normalized_bottom_left_v1"))
        c.commit()
    finally:
        c.close()


@app.on_event("startup")
def startup() -> None:
    init_db()
    init_auth_schema()
    load_jobs()


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def persist_job_locked(jid: str, force: bool = False) -> None:
    now = time.time()
    if not force and now - job_last_persisted.get(jid, 0) < 1.0:
        return
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    destination = JOB_DIR / f"{jid}.json"
    temporary = JOB_DIR / f".{jid}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(jsons(jobs[jid]), encoding="utf-8")
    temporary.replace(destination)
    job_last_persisted[jid] = now


def load_jobs() -> None:
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    with jobs_lock:
        for path in JOB_DIR.glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if now - float(value.get("updated_at", now)) > JOB_RETENTION_SECONDS:
                    path.unlink(missing_ok=True)
                    continue
                if value.get("status") in {"queued", "running"}:
                    value.update(status="error", error="Job interrupted by backend restart", updated_at=now)
                jid = str(value["id"])
                jobs[jid] = value
                persist_job_locked(jid, force=True)
            except Exception:
                logger.exception("Ignoring invalid job journal %s", path.name)


def create_job(kind: str, **values: Any) -> dict[str, Any]:
    now = time.time()
    with jobs_lock:
        expired = [key for key, value in jobs.items() if value.get("status") in {"done", "error", "cancelled"} and now - value.get("updated_at", now) > JOB_RETENTION_SECONDS]
        for key in expired:
            jobs.pop(key, None)
            job_last_persisted.pop(key, None)
            (JOB_DIR / f"{key}.json").unlink(missing_ok=True)
        jid = uuid.uuid4().hex[:12]
        jobs[jid] = {"id": jid, "kind": kind, "status": "queued", "created_at": now, "updated_at": now, **values}
        persist_job_locked(jid, force=True)
        return jobs[jid]


def update_job(jid: str, **values: Any) -> None:
    with jobs_lock:
        if jid in jobs:
            jobs[jid].update(updated_at=time.time(), **values)
            persist_job_locked(jid, force=jobs[jid].get("status") in {"done", "error", "cancelled"})

def jsons(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def numeric_field(payload: dict[str, Any], name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(payload.get(name, default))
    except (TypeError, ValueError):
        raise HTTPException(422, f"{name} must be numeric")
    if not minimum <= value <= maximum:
        raise HTTPException(422, f"{name} must be between {minimum} and {maximum}")
    return value


def integer_field(payload: dict[str, Any], name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(payload.get(name, default))
    except (TypeError, ValueError):
        raise HTTPException(422, f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise HTTPException(422, f"{name} must be between {minimum} and {maximum}")
    return value


def safe_root(raw: str) -> Path:
    # Security: prevent path traversal and access outside allowed root
    p = Path(raw).expanduser().resolve()
    if not p.is_dir():
        raise HTTPException(400, "Directory not found or inaccessible")
    # Strict check: path must be exactly ALLOWED_ROOT or a direct child/descendant
    try:
        p.relative_to(ALLOWED_ROOT)  # Will raise ValueError if not relative
    except ValueError:
        raise HTTPException(403, "Path is outside the configured workspace")
    return p


def resolve_device(requested: str | None = None) -> str:
    """Resolve auto to CUDA:0 when Torch sees a GPU; never silently guess CPU."""
    requested = requested or os.getenv("PROTOLABEL_DEVICE", "auto")
    if requested not in {"", "auto", "cuda", "cuda:0"}:
        return requested
    try:
        import torch
        if torch.cuda.is_available():
            return "0"
    except Exception:
        pass
    return "cpu"


def runtime_info() -> dict[str, Any]:
    info: dict[str, Any] = {"device": resolve_device(), "cuda_available": False, "gpu_name": None, "gpu_memory_gb": None}
    try:
        import torch
        info["cuda_available"] = bool(torch.cuda.is_available())
        if info["cuda_available"]:
            index = torch.cuda.current_device()
            info["device"] = f"cuda:{index}"
            info["gpu_name"] = torch.cuda.get_device_name(index)
            info["gpu_memory_gb"] = round(torch.cuda.get_device_properties(index).total_memory / 2**30, 2)
    except Exception:
        logger.exception("Unable to inspect runtime device")
    return info


def model_options() -> list[dict[str, Any]]:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    supported = {".pt", ".onnx", ".engine", ".torchscript"}
    result = []
    for path in sorted(MODEL_DIR.iterdir(), key=lambda item: item.name.casefold()):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in supported:
            continue
        result.append({
            "id": path.name,
            "label": path.stem.replace("_", " ").replace("-", " "),
            "format": path.suffix.lower().lstrip("."),
            "size_mb": round(path.stat().st_size / 1024**2, 1),
            "available": True,
        })
    return result


def get_project(c: sqlite3.Connection, pid: str):
    p = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not p:
        raise HTTPException(404, "Không tìm thấy project")
    return p


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "protolabel"}


@app.get("/api/models")
def models():
    return {"models": model_options(), "runtime": runtime_info()}


@app.get("/api/runtime")
def runtime():
    return runtime_info()


@app.get("/api/projects")
def projects():
    c = db()
    rows = c.execute("SELECT p.id,p.name,p.root,p.created_at,COUNT(i.id) total,SUM(i.status='labeled') labeled,SUM(i.status='review') review FROM projects p LEFT JOIN images i ON p.id=i.project_id GROUP BY p.id ORDER BY p.created_at DESC").fetchall()
    c.close()
    return {"projects": [dict(r) for r in rows]}


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    c = db()
    get_project(c, pid)  # Check project exists
    try:
        c.execute("DELETE FROM boxes WHERE image_id IN (SELECT id FROM images WHERE project_id=?)", (pid,))
        c.execute("DELETE FROM images WHERE project_id=?", (pid,))
        c.execute("DELETE FROM projects WHERE id=?", (pid,))
        c.commit()
        c.close()
        return {"status": "ok", "message": f"Project {pid} deleted"}
    except Exception as exc:
        c.close()
        logger.exception("Project deletion failed")
        raise HTTPException(500, "Project deletion failed")


def _scan_project_job(jid: str, root: Path, name: str, classes: list[str]):
    update_job(jid, status="running", stage="discovering", detail="Đang tìm file ảnh…")
    try:
        files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in EXTS]
        update_job(jid, stage="indexing", total=len(files), processed=0, detail=f"Đã tìm thấy {len(files):,} file; đang đọc metadata…")
        pid = uuid.uuid4().hex[:12]
        c = db()
        c.execute("INSERT INTO projects(id,name,root,classes) VALUES(?,?,?,?)", (pid, name[:100], str(root), jsons(classes)))
        count = 0
        for n, path in enumerate(files, 1):
            image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if image is not None:
                h, w = image.shape[:2]
                rel = str(path.relative_to(root))
                iid = hashlib.sha1(f"{pid}:{rel}".encode()).hexdigest()[:20]
                c.execute("INSERT OR IGNORE INTO images VALUES(?,?,?,?,?,?)", (iid, pid, rel, int(w), int(h), "unlabeled"))
                count += 1
            update_job(jid, processed=n, detail=f"Đọc metadata {n:,}/{len(files):,}")
        c.commit(); c.close()
        update_job(jid, status="done", stage="complete", project_id=pid, image_count=count, root=str(root), name=name, detail=f"Đã lập chỉ mục {count:,} ảnh")
    except Exception as exc:
        update_job(jid, status="error", error="Project scan failed", detail="Không thể quét thư mục")


@app.post("/api/projects")
def create_project(payload: dict[str, Any]):
    root = safe_root(str(payload.get("root_path", "")))
    # Allow custom classes, fall back to defaults only if empty
    classes = payload.get("classes")
    if not classes or not isinstance(classes, list) or not all(isinstance(c, str) for c in classes):
        classes = ["object"]  # Minimal default instead of PPE-specific
    name = str(payload.get("name") or root.name)[:100]
    c = db()
    duplicate = c.execute("SELECT id FROM projects WHERE root=? LIMIT 1", (str(root),)).fetchone()
    c.close()
    if duplicate:
        raise HTTPException(409, "A project already exists for this directory")
    job_data = create_job("project_scan", stage="queued", processed=0, total=0, error=None, detail="Đang xếp hàng quét dữ liệu…", name=name, root=str(root))
    jid = job_data["id"]
    pool.submit(_scan_project_job, jid, root, name, classes)
    return jobs[jid]


@app.post("/api/projects/upload")
async def upload_project(files: list[UploadFile] = File(...), name: str = ""):
    """Upload a browser-selected folder, then index it as a normal project."""
    if not files:
        raise HTTPException(400, "Chưa chọn thư mục ảnh")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(413, f"Folder upload is limited to {MAX_UPLOAD_FILES:,} files")
    declared_bytes = sum(int(file.size or 0) for file in files)
    if declared_bytes > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Folder upload is limited to {MAX_UPLOAD_BYTES / 1024**3:.1f} GiB")
    upload_root = DATA / "dataset_uploads" / uuid.uuid4().hex[:12]
    upload_root.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    saved = 0
    try:
        for file in files:
            raw_name = (file.filename or "image").replace("\\", "/")
            parts = [part for part in Path(raw_name).parts if part not in {"", ".", ".."}]
            if not parts:
                continue
            rel = Path(*parts)
            if rel.suffix.lower() not in EXTS:
                continue
            destination = upload_root / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as handle:
                while chunk := await file.read(4 * 1024 * 1024):
                    total_bytes += len(chunk)
                    if total_bytes > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, f"Folder upload is limited to {MAX_UPLOAD_BYTES / 1024**3:.1f} GiB")
                    handle.write(chunk)
            if cv2.imread(str(destination), cv2.IMREAD_UNCHANGED) is None:
                destination.unlink(missing_ok=True)
                continue
            saved += 1
    except Exception:
        import shutil
        shutil.rmtree(upload_root, ignore_errors=True)
        raise
    if not saved:
        import shutil
        shutil.rmtree(upload_root, ignore_errors=True)
        raise HTTPException(400, "Thư mục không có file ảnh hỗ trợ")
    name = name.strip() or upload_root.name
    job_data = create_job("project_scan", stage="queued", processed=0, total=0, error=None, detail=f"Đã upload {saved:,} file; đang xếp hàng quét…", name=name, root=str(upload_root), uploaded_files=saved, uploaded_bytes=total_bytes)
    jid = job_data["id"]
    pool.submit(_scan_project_job, jid, upload_root, name, ["person", "helmet", "glasses", "gloves", "shoes", "safety-vest"])
    return job_data


@app.get("/api/projects/{pid}")
def project(pid: str):
    c = db(); p = get_project(c, pid)
    stat = c.execute("SELECT COUNT(*) total,SUM(status='unlabeled') unlabeled,SUM(status='review') review,SUM(status='labeled') labeled FROM images WHERE project_id=?", (pid,)).fetchone(); c.close()
    return {**dict(p), "classes": json.loads(p["classes"]), "stats": dict(stat)}

@app.get("/api/projects/{pid}/reid-identities")
def reid_identities(pid: str):
    """Return project-wide Re-ID labels stored on person boxes."""
    c = db(); get_project(c, pid)
    rows = c.execute(
        "SELECT b.attrs FROM boxes b JOIN images i ON i.id=b.image_id WHERE i.project_id=?",
        (pid,),
    ).fetchall()
    c.close()
    counts: dict[str, int] = {}
    for row in rows:
        try:
            value = str((json.loads(row["attrs"]) or {}).get("reid_id", "")).strip()
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if value:
            counts[value] = counts.get(value, 0) + 1

    def sort_key(item: tuple[str, int]):
        value = item[0]
        suffix = value[1:] if value[:1].upper() == "P" else ""
        return (0, int(suffix)) if suffix.isdigit() else (1, value.casefold())

    identities = [{"id": identity, "box_count": count}
                  for identity, count in sorted(counts.items(), key=sort_key)]
    return {"identities": identities, "count": len(identities)}


@app.get("/api/projects/{pid}/images")
def image_list(pid: str, status: str = "all", search: str = "", page: int = 1, page_size: int = 80):
    c = db(); get_project(c, pid); page, page_size = max(1, page), max(1, min(page_size, 200))
    where, args = ["project_id=?"], [pid]
    if status in {"unlabeled", "review", "labeled"}: where.append("status=?"); args.append(status)
    if search: where.append("rel_path LIKE ?"); args.append(f"%{search}%")
    clause = " AND ".join(where)
    total = c.execute(f"SELECT COUNT(*) FROM images WHERE {clause}", args).fetchone()[0]
    rows = c.execute(f"SELECT id,rel_path,width,height,status FROM images WHERE {clause} ORDER BY rel_path LIMIT ? OFFSET ?", [*args, page_size, (page-1)*page_size]).fetchall(); c.close()
    return {"images": [{**dict(r), "url": f"/api/projects/{pid}/media/{r['id']}"} for r in rows], "total": total, "page": page, "page_size": page_size}


@app.get("/api/projects/{pid}/images/{iid}")
def image_detail(pid: str, iid: str, request: Request):
    c = db(); p = get_project(c, pid); row = c.execute("SELECT * FROM images WHERE id=? AND project_id=?", (iid, pid)).fetchone()
    if not row: raise HTTPException(404, "Không tìm thấy ảnh")
    boxes = c.execute("SELECT * FROM boxes WHERE image_id=? ORDER BY rowid", (iid,)).fetchall(); c.close()
    record_image_open(request.state.user["id"], iid)
    return {**dict(row), "url": f"/api/projects/{pid}/media/{iid}", "classes": json.loads(p["classes"]), "boxes": [{"id": b["id"], "cls_name": b["cls_name"], "bbox": [b["x1"], b["y1"], b["x2"], b["y2"]], "confidence": b["confidence"], "source": b["source"], "attributes": json.loads(b["attrs"])} for b in boxes]}


@app.get("/api/projects/{pid}/media/{iid}")
def media(pid: str, iid: str):
    c = db(); p = get_project(c, pid); row = c.execute("SELECT rel_path FROM images WHERE id=? AND project_id=?", (iid, pid)).fetchone(); c.close()
    if not row: raise HTTPException(404, "Không tìm thấy ảnh")
    root, path = Path(p["root"]).resolve(), (Path(p["root"]) / row["rel_path"]).resolve()
    if root not in path.parents or not path.is_file(): raise HTTPException(404, "File ảnh không tồn tại")
    return FileResponse(path)


@app.put("/api/projects/{pid}/images/{iid}/boxes")
def save_boxes(pid: str, iid: str, payload: dict[str, Any], request: Request):
    c = db(); get_project(c, pid)
    if not c.execute("SELECT 1 FROM images WHERE id=? AND project_id=?", (iid, pid)).fetchone(): raise HTTPException(404, "Image not found")
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("DELETE FROM boxes WHERE image_id=?", (iid,))
        for a in payload.get("boxes", []):
            bbox_raw = a.get("bbox", [0,0,0,0])
            try:
                b = [float(v) for v in bbox_raw]
            except (ValueError, TypeError):
                raise ValueError(f"Invalid bbox values (not numeric): {bbox_raw}")
            if len(b) != 4:
                raise ValueError(f"Bbox must have 4 values, got {len(b)}")
            if not all(0.0 <= v <= 1.0 for v in b):
                raise ValueError(f"Bbox values must be in [0, 1], got {b}")
            if b[2] <= b[0] or b[3] <= b[1]:
                raise ValueError(f"Invalid bbox: x2 must be > x1, y2 must be > y1")
            attributes = dict(a.get("attributes") or {})
            reid_id = str(attributes.get("reid_id", "")).strip()[:64]
            if reid_id:
                attributes["reid_id"] = reid_id
            else:
                attributes.pop("reid_id", None)
            c.execute("INSERT INTO boxes VALUES(?,?,?,?,?,?,?,?,?,?)", (a.get("id") or uuid.uuid4().hex[:16], iid, str(a.get("cls_name", "object")), *b, a.get("confidence"), str(a.get("source", "manual")), jsons(attributes)))
        status = payload.get("status") or ("labeled" if payload.get("boxes") else "unlabeled")
        if status not in {"unlabeled", "review", "labeled"}: status = "labeled"
        c.execute("UPDATE images SET status=? WHERE id=?", (status, iid))
        c.commit()
    except Exception as exc:
        c.execute("ROLLBACK")
        raise HTTPException(400, f"Save failed: {str(exc)}")
    finally: c.close()
    record_event(request.state.user["id"], "image_save", pid, iid, len(payload.get("boxes", [])), track_elapsed=True)
    return image_detail(pid, iid, request)


def predict(model_id: str, path: Path, conf: float, iou: float, device: str | None):
    opts = {m["id"]: m for m in model_options()}
    if model_id not in opts or not opts[model_id]["available"]: raise RuntimeError(f"Checkpoint chưa có: {model_id}")
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("Cài ultralytics trong môi trường sgdetr trước khi chạy prelabel") from exc
    with model_lock:
        model = model_cache.get(model_id)
        if model is None: model = model_cache.setdefault(model_id, YOLO(str(MODEL_DIR / model_id)))
    selected_device = resolve_device(device)
    kwargs = {"source": str(path), "conf": conf, "iou": iou, "verbose": False, "device": selected_device}
    if selected_device != "cpu": kwargs["half"] = True
    result = model.predict(**kwargs)[0]
    names, h, w = result.names or {}, result.orig_shape[0], result.orig_shape[1]
    out = []
    if result.boxes is not None:
        for box, score, cls in zip(result.boxes.xyxy.cpu().tolist(), result.boxes.conf.cpu().tolist(), result.boxes.cls.cpu().tolist()):
            x1,y1,x2,y2 = box
            out.append({"id": uuid.uuid4().hex[:16], "cls_name": str(names.get(int(cls), int(cls))), "bbox": [x1/w,1-y2/h,x2/w,1-y1/h], "confidence": float(score), "source": model_id, "attributes": {}})
    return out


def prelabel_job(job_id: str, pid: str, ids: list[str], model_id: str, conf: float, iou: float, replace: bool, device: str | None):
    update_job(job_id, status="running"); c = db()
    try:
        p = get_project(c, pid); root = Path(p["root"])
        for n, iid in enumerate(ids, 1):
            row = c.execute("SELECT rel_path FROM images WHERE id=? AND project_id=?", (iid, pid)).fetchone()
            if not row: continue
            try:
                boxes = predict(model_id, (root / row["rel_path"]).resolve(), conf, iou, device)
                c.execute("BEGIN IMMEDIATE")
                if replace: c.execute("DELETE FROM boxes WHERE image_id=? AND source!=?", (iid, "manual"))
                for a in boxes:
                    b=a["bbox"]; c.execute("INSERT INTO boxes VALUES(?,?,?,?,?,?,?,?,?,?)", (a["id"],iid,a["cls_name"],*b,a["confidence"],a["source"],jsons(a["attributes"])))
                c.execute("UPDATE images SET status='review' WHERE id=?", (iid,))
                c.commit()
            except Exception as e:
                c.execute("ROLLBACK")
                update_job(job_id, error=f"Image {iid} failed")
                raise
            update_job(job_id, processed=n)
        update_job(job_id, status="done")
    except Exception:
        update_job(job_id, status="error", error="Prelabel failed; check backend logs")
    finally: c.close()


@app.post("/api/projects/{pid}/prelabel")
def start_prelabel(pid: str, payload: dict[str, Any], request: Request):
    c = db()
    get_project(c, pid)
    requested_ids = payload.get("image_ids")
    if requested_ids is not None and (not isinstance(requested_ids, list) or not all(isinstance(value, str) for value in requested_ids)):
        c.close()
        raise HTTPException(422, "image_ids must be a list of strings")
    if requested_ids:
        placeholders = ",".join("?" for _ in requested_ids)
        ids = [row["id"] for row in c.execute(f"SELECT id FROM images WHERE project_id=? AND id IN ({placeholders})", [pid, *requested_ids]).fetchall()]
    else:
        ids = [row["id"] for row in c.execute("SELECT id FROM images WHERE project_id=? AND status <> ? ORDER BY rel_path", (pid, "labeled")).fetchall()]
    c.close()
    if not ids:
        raise HTTPException(400, "Không có ảnh để prelabel")
    conf = numeric_field(payload, "conf", .25, .01, 1.0)
    iou = numeric_field(payload, "iou", .7, .01, 1.0)
    models_by_id = {item["id"]: item for item in model_options()}
    model_id = str(payload.get("model_id") or next(iter(models_by_id), ""))
    if model_id not in models_by_id or not models_by_id[model_id]["available"]:
        raise HTTPException(422, "Selected checkpoint is unavailable")
    job_data = create_job("prelabel", processed=0, total=len(ids), error=None, device=resolve_device(payload.get("device")))
    record_event(request.state.user["id"], "prelabel_start", pid, value=len(ids))
    pool.submit(prelabel_job, job_data["id"], pid, ids, model_id, conf, iou, bool(payload.get("replace", True)), payload.get("device"))
    return job_data


@app.get("/api/jobs/{jid}")
def job(jid: str):
    if jid not in jobs: raise HTTPException(404, "Không tìm thấy job")
    return jobs[jid]



def _video_project_job(jid: str, video_path: str, name: str, target_frames: int):
    update_job(jid, status="running")
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if total <= 0:
        error_msg = f"Cannot read video: frame_count={total}, fps={fps}. File may be corrupted."
        update_job(jid, status="error", error="Video metadata is invalid")
        cap.release()
        return
    if fps <= 0:
        error_msg = f"Invalid FPS: {fps}. Video file may be corrupted."
        update_job(jid, status="error", error="Video metadata is invalid")
        cap.release()
        return
    count = total if target_frames <= 0 else min(total, target_frames)
    indices = sorted({round(i * (total - 1) / max(1, count - 1)) for i in range(count)})
    pid = uuid.uuid4().hex[:12]
    out_root = DATA / "video_frames" / pid
    out_root.mkdir(parents=True, exist_ok=True)
    c = db()
    try:
        classes = ["person", "helmet", "glasses", "gloves", "shoes", "safety-vest"]
        c.execute("INSERT INTO projects(id,name,root,classes) VALUES(?,?,?,?)", (pid, name[:100], str(out_root), jsons(classes)))
        saved = 0

        def save_frame(frame_no: int, frame) -> None:
            nonlocal saved
            filename = f"frame_{frame_no:08d}.jpg"
            path = out_root / filename
            if cv2.imwrite(str(path), frame):
                h, w = frame.shape[:2]
                iid = hashlib.sha1(f"{pid}:{filename}".encode()).hexdigest()[:20]
                c.execute("INSERT INTO images VALUES(?,?,?,?,?,?)", (iid, pid, filename, int(w), int(h), "unlabeled"))
                saved += 1
                update_job(jid, processed=saved)

        # Seeking tránh giải mã hàng trăm nghìn frame khi người dùng chỉ cần
        # vài chục/vài trăm frame. Với sampling dày, đọc tuần tự nhanh hơn.
        sparse = len(indices) < total * 0.35
        if sparse:
            for frame_no in indices:
                if not cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no):
                    jobs[jid]["error"] = f"Cannot seek to frame {frame_no}"
                    raise RuntimeError(f"Video seek failed at frame {frame_no}")
                ok, frame = cap.read()
                if ok:
                    save_frame(frame_no, frame)
        else:
            wanted = set(indices)
            frame_no = 0
            while wanted:
                ok, frame = cap.read()
                if not ok:
                    break
                if frame_no in wanted:
                    save_frame(frame_no, frame)
                    wanted.remove(frame_no)
                frame_no += 1
        c.commit()
        update_job(jid, status="done", project_id=pid, total=saved, source=video_path, fps=fps, source_frames=total)
    except Exception as exc:
        c.rollback()
        logger.exception("Video project job failed")
        update_job(jid, status="error", error="Video processing failed; check backend logs")
    finally:
        cap.release()
        c.close()



def _enqueue_video(video: Path, name: str, target: int):
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        cap.release()
        raise HTTPException(400, "Không mở được video")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    cap.release()
    if total <= 0:
        raise HTTPException(400, "Video không có frame hợp lệ")
    if target < 0 or target > 1_000_000:
        raise HTTPException(400, "target_frames phải từ 0 đến 1.000.000; 0 = lấy toàn bộ frame")
    target = min(total, target) if target else total
    job_data = create_job("video", processed=0, total=target, error=None, source_frames=total, fps=fps)
    pool.submit(_video_project_job, job_data["id"], str(video), name or video.stem, target)
    return job_data


@app.post("/api/video-projects")
def start_video_project(payload: dict[str, Any]):
    raw = str(payload.get("video_path", ""))
    raw_path = Path(raw).expanduser().resolve()
    video = (safe_root(str(raw_path.parent)) / raw_path.name).resolve()
    if not video.is_file() or video.suffix.lower() not in {".mp4", ".avi", ".mov", ".mkv", ".webm"}:
        raise HTTPException(400, "Video không tồn tại hoặc định dạng không hỗ trợ")
    target = integer_field(payload, "target_frames", 0, 0, 1_000_000)
    return _enqueue_video(video, str(payload.get("name") or video.stem), target)


@app.post("/api/video-projects/upload")
async def upload_video_project(file: UploadFile = File(...), target_frames: int = 0, name: str = ""):
    """Upload video từ máy LAN hiện tại rồi chạy sampler nền."""
    filename = Path(file.filename or "video.mp4").name
    ext = Path(filename).suffix.lower()
    if ext not in {".mp4", ".avi", ".mov", ".mkv", ".webm"}:
        raise HTTPException(400, "Chỉ hỗ trợ MP4, AVI, MOV, MKV, WEBM")
    upload_dir = DATA / "video_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dst = upload_dir / f"{uuid.uuid4().hex[:12]}-{filename}"
    size = 0
    try:
        with dst.open("wb") as handle:
            while chunk := await file.read(4 * 1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"Video upload is limited to {MAX_UPLOAD_BYTES / 1024**3:.1f} GiB")
                handle.write(chunk)
    except Exception:
        dst.unlink(missing_ok=True)
        raise
    return _enqueue_video(dst, name or Path(filename).stem, target_frames)


@app.get("/api/projects/{pid}/export")
def export_project(pid: str, format: str = "yolo"):
    """Export annotations only, preserving source-relative paths in a zip."""
    if format not in {"yolo", "json"}:
        raise HTTPException(400, "format phải là yolo hoặc json")
    c = db(); p = get_project(c, pid)
    rows = c.execute("SELECT * FROM images WHERE project_id=? ORDER BY rel_path", (pid,)).fetchall()
    classes = json.loads(p["classes"])
    out = Path(tempfile.mkstemp(prefix=f"protolabel-{pid}-", suffix=".zip", dir=DATA)[1])
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        if format == "yolo": z.writestr("classes.txt", "\n".join(classes) + "\n")
        for row in rows:
            boxes = c.execute("SELECT * FROM boxes WHERE image_id=?", (row["id"],)).fetchall()
            if format == "json":
                z.writestr(f"annotations/{row['rel_path']}.json", jsons({"image": row["rel_path"], "width": row["width"], "height": row["height"], "boxes": [{"class": b["cls_name"], "bbox": [b["x1"],b["y1"],b["x2"],b["y2"]], "confidence": b["confidence"], "source": b["source"], "attributes": json.loads(b["attrs"]), "reid_id": (json.loads(b["attrs"]) or {}).get("reid_id")} for b in boxes]}))
            else:
                lines=[]
                for b in boxes:
                    if b["cls_name"] not in classes: continue
                    cx=(b["x1"]+b["x2"])/2; cy=1-(b["y1"]+b["y2"])/2
                    lines.append(f"{classes.index(b['cls_name'])} {cx:.6f} {cy:.6f} {(b['x2']-b['x1']):.6f} {(b['y2']-b['y1']):.6f}")
                z.writestr(f"labels/{Path(row['rel_path']).with_suffix('.txt')}", "\n".join(lines) + ("\n" if lines else ""))
    c.close()
    return FileResponse(out, media_type="application/zip", filename=f"{p['name']}-{format}.zip", background=BackgroundTask(out.unlink, missing_ok=True))
