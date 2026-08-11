# ProtoLabel

*(For the primary, most up-to-date documentation, see [README.md](README.md) in Vietnamese. This is a faithful English summary/translation for contributors and reviewers.)*

Self-hosted labeling application, independent of CVAT/Roboflow. Works on images and video: scans a folder, prelabels bbox/segment/skeleton/3D using a local checkpoint, lets you edit annotations with mouse or keyboard, and exports/imports lossless JSON.

## Running without Docker

Do not run this alongside Docker Compose at the same time — the frontend uses the same port (`8101`). The local backend binds only `127.0.0.1:8100` and must not be exposed to the LAN.

### First-time setup

```bash
cd /path/to/ProtoLabel_Tool
conda activate sgdetr
python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
cp -n .env.example .env
```

Open `.env` and set `PROTOLABEL_HOST_WORKSPACE` to the parent directory that contains `data/` and the datasets the app should be allowed to access. Sessions default to 7 days; adjust `PROTOLABEL_SESSION_TTL_SECONDS` if needed. The default scan limit is 250,000 images (`PROTOLABEL_MAX_SCAN_IMAGES`); past that threshold a scan job stops with a clear error instead of loading unbounded data into RAM.

Failed logins are rate-limited to 5 attempts per 15 minutes per username+IP by default. Tune with `PROTOLABEL_LOGIN_MAX_ATTEMPTS` and `PROTOLABEL_LOGIN_WINDOW_SECONDS`.

### Run backend and frontend via script

```bash
chmod +x scripts/run_all.sh
./scripts/run_all.sh
```

The script reads `.env`, uses the `sgdetr` conda environment if present, runs the backend locally at `127.0.0.1:8100`, the frontend at `0.0.0.0:8101`, and writes logs to `logs/`. Open `http://<SERVER-IP>:8101`; the browser will show the Login/Register screen. Press `Ctrl+C` to stop both services.

If Ubuntu's UFW is enabled, only open the frontend port:

```bash
sudo ufw allow 8101/tcp
```

Do not expose port `8100` to the LAN.

## Accounts and performance tracking

When the database has no users yet, the backend automatically creates an initial admin account:

```text
username: admin
password: admin
```

The first login forces a password change to at least 10 characters. Do not keep using `admin/admin`. Other users register from the Register screen and default to the `annotator` role.

Admins can open **Dashboard** in the header to:

- See the distinct image count with real annotation changes, the latest bbox snapshot, prelabel runs per image, active time, and last-active time per user. Repeated saves with no actual change are not counted.
- Filter the dashboard by project and by 7 days, 30 days, or all time.
- Change a user's role (`admin`/`annotator`), lock or unlock accounts.
- Reset a password; the affected user must change it on next login.
- Enable or disable new registrations.

Users can change their own password from the **Password** button in the header. Passwords are stored only as `scrypt` hashes; sessions use HttpOnly/SameSite cookies. Performance tracking starts from the moment the accounts feature was deployed — older annotations are not modified or retroactively attributed to a user.

## Video import and frame extraction

From the start screen, you can enter a video path on the **server** or pick a video from the machine currently viewing the browser. Then choose how many frames to sample:

- `300`: evenly sample 300 frames across the whole video.
- `0`: extract every frame.
- The job runs in the background and automatically opens as a labeling project when done.

A video referenced by path must live inside `PROTOLABEL_HOST_WORKSPACE`. A video picked from a LAN machine is uploaded to the server before frame extraction. For very long videos, start with 300–2,000 frames for a quick review before scaling up.

## Quick workflow

1. Enter an image folder path and create a project.
2. Choose BBox, Segment, Skeleton, or 3D on the toolbar.
3. Enter the classes to look for (e.g. `person`), pick a compatible checkpoint, and click **Prelabel this page** or **Prelabel entire project**.
4. Prelabel only updates AI predictions of the same annotation type and requested class(es). Manual annotations and other existing classes (e.g. `car`) are preserved.
5. AI annotations use a dashed orange outline; manual annotations use a solid green outline.
6. Edit on the canvas or in the inspector, press `Ctrl+S` to save and mark as reviewed.
7. Use **Export JSON** to keep full segment/skeleton/3D data, and **Import JSON** to merge it back into the server in a later session. YOLO export is bbox-only.

Default shortcuts: `A/D` previous/next image, `W/G/K/C` select bbox/segment/skeleton/3D, `Q` close the annotation editor, `Shift+W` create a default annotation centered on the image, arrow keys nudge the selected annotation by 1px (`Shift`+arrow: 10px), `Backspace/Delete` remove, `1–9` change class, `P` prelabel the current image, `Ctrl+S` save, `Z/X` pick zoom in/out then click where to zoom. Each user can remap shortcuts from the settings screen; the shortcut recorder accepts `Ctrl`, `Shift`, `Alt`, `Meta`, plus the main key directly. When a combination is bound to an app action (e.g. `Ctrl+A`), the app suppresses the browser's default behavior and handles it first.

Selecting an annotation from the canvas or the Outliner switches the editor into an isolated mode where only that annotation receives interaction. Drag a bbox's four handles, or the individual vertices/keypoints of a segment, skeleton, or cuboid, to adjust it; click `×` in **Edit annotation** before selecting a different annotation.

## GPU prelabeling

The backend automatically selects `cuda:0` when `torch.cuda.is_available()` returns true. With a GPU available, Ultralytics runs prelabeling with CUDA acceleration; the toolbar shows a `GPU · ...` chip and the job reports the actual device used. Check directly with:

```bash
conda activate sgdetr
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

Force a specific device if needed:

```bash
PROTOLABEL_DEVICE=0 ./scripts/run_all.sh
# Intentional CPU fallback:
PROTOLABEL_DEVICE=cpu ./scripts/run_all.sh
```

Initial video/JPEG decoding still runs on CPU via FFmpeg/OpenCV; tensor preprocessing and model inference run on the GPU.

## Remote CAP/model API

ProtoLabel also reads `remote-models.json` and merges configured HTTP services into the same checkpoint list as local models. See `README.md` for the current field-level format and `CONTRIBUTING.md` for the security note on this integration point. **If you enable this feature, do not commit real internal endpoints/IPs to a public fork — treat `remote-models.json` like `.env`: keep an example/template in version control and the real file local-only.**

## Model registry

The Prelabel model list is never hardcoded. The backend scans the `models/` directory on every call to `/api/models` and lists `.pt`, `.onnx`, `.engine`, `.torchscript` files. Drop a new checkpoint into that directory and refresh the model list/page — no backend changes needed.

Checkpoints are not stored in Git (too large). Download the default Ultralytics YOLO26 set with:

```bash
cd /path/to/ProtoLabel_Tool
conda activate sgdetr
python scripts/download_models.py
```

The script creates `models/` and skips files that already exist; use `--force` to re-download. The default set includes a text-promptable YOLOE segmentation checkpoint, YOLO26 pose, and YOLO26 depth. Custom models must be Ultralytics-compatible, otherwise inference will fail for that model.

- **Segment:** YOLOE takes a text list of classes and returns instance polygons.
- **Skeleton:** a pose checkpoint returns the 17 COCO keypoints; the classes you enter are used to filter the checkpoint's output.
- **3D:** the detector produces an 8-point cuboid projected onto the image, and a depth checkpoint adds an estimated depth. This is **not** a camera-calibrated 3D cuboid — accurate world coordinates require camera intrinsics/extrinsics and a dedicated 3D model.

## Data and license

Annotations, projects, users, and history are stored in `<PROTOLABEL_HOST_WORKSPACE>/data/prot0label.sqlite3` (SQLite, WAL mode). Images scanned from the server are never copied; images/videos uploaded from the browser live under `data/`. Rebuilding the image/container does not lose data because `/workspace` is a bind mount — do not run `docker compose down -v` or delete the workspace without checking your backups first.

The backend creates an online SQLite backup at startup and every 24 hours, keeping 14 days by default. In Docker, these are written to `PROTOLABEL_HOST_BACKUP_DIR`, separate from the workspace volume. Point that directory at a different disk/NAS and sync it offsite — a copy on the same disk does not protect against a disk failure. `PRAGMA optimize` also runs on the same maintenance schedule.

SQLite is a good fit for a single instance and a small team. The current configuration is functionally tested but has not been load-tested to a specific concurrent-user number — watch for `database is locked` and rising save latency. If you need multiple backend replicas or heavy concurrent write load, migrate to PostgreSQL rather than arbitrarily increasing worker count.

This is original code for this project; it does not use Roboflow/CVAT commercial code or services. **Note on dependency licensing:** the backend depends on [Ultralytics](https://github.com/ultralytics/ultralytics), which is licensed AGPL-3.0 (or a commercial Ultralytics Enterprise license). Check the license of every checkpoint, library, and dataset you use before distributing a deployment — see [LICENSE](LICENSE) for how this affects ProtoLabel's own license choice.

All image coordinates (bbox, keypoints, crop bbox, and mask centers) use normalized floats in `0..1`, origin at the top-left. A bbox is `[x, y, w, h]`, where `x, y` is the top-left corner and `w, h` is the box size, both divided by the frame width/height. Consumers convert to pixels by multiplying the x-axis by `frame_w` and the y-axis by `frame_h`.

## Icon assets

ProtoLabel uses local animated GIFs from Flaticon for folder, upload, video, and loading icons. Attribution is shown directly in the app's footer; asset source: [Flaticon animated icons](https://www.flaticon.com/animated-icons).

## Running with Docker Compose

The Docker package includes a FastAPI/Ultralytics backend and a production Nginx frontend. The backend runs as non-root, stays on Compose's internal network, and does not publish port `8100` — users only reach port `8101` through Nginx.

### Build and start

```bash
cd /path/to/ProtoLabel_Tool
cp -n .env.example .env
```

Before running, edit `.env`:

- `PROTOLABEL_HOST_WORKSPACE`: absolute path to the workspace containing `data/` and your datasets.
- `PROTOLABEL_HOST_BACKUP_DIR`: a separate backup directory that already exists and is writable by the user running Docker.
- `PROTOLABEL_UID` and `PROTOLABEL_GID`: the output of `id -u` and `id -g`, so the backend does not run as root but can still write the database.

Then:

```bash
mkdir -p models backups
python scripts/download_models.py
sudo docker compose build
sudo docker compose up -d
sudo docker compose ps
```

The backend must report `healthy`. Open `http://<SERVER-IP>:8101`; the Login/Register screen will appear. Log in the first time with `admin/admin` and change the password immediately. `/docs`, the API, media, and export all require a valid session.

### Logs and rebuilds

```bash
sudo docker compose logs -f
sudo docker compose logs --tail=200 backend

# Rebuild only the frontend
sudo docker compose build frontend
sudo docker compose up -d --force-recreate frontend

# Rebuild only the backend
sudo docker compose build backend
sudo docker compose up -d --force-recreate backend

# Rebuild everything
sudo docker compose build
sudo docker compose up -d --force-recreate
```

Stop with `sudo docker compose down`. Compose requires the NVIDIA Container Toolkit by default because the backend declares `gpus: all`. For CPU-only, remove the GPU configuration and set `PROTOLABEL_DEVICE: cpu` for the backend. Do not use `docker compose down -v` unless you have already checked that you don't need the data.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

See [LICENSE](LICENSE).
