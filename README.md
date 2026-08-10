# ProtoLabel

Ứng dụng labeling self-hosted, tách khỏi SafeGuard/CVAT/Roboflow. Dùng cho ảnh và video đã tách frame: quét thư mục ảnh, prelabel bằng checkpoint local, sửa bbox bằng keyboard và export YOLO/JSON.

## Chạy không Docker

Không chạy đồng thời cách này với Docker Compose vì frontend cùng dùng cổng `8101`. Backend local chỉ bind `127.0.0.1:8100` và không được mở ra LAN.

### Cài dependency và cấu hình lần đầu

```bash
cd /duong/dan/toi/ProtoLabel_Tool
conda activate sgdetr
python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
cp -n .env.example .env
```

Mở `.env`, đặt `PROTOLABEL_AUTH_PASSWORD` thành mật khẩu dài, riêng biệt; không commit hoặc chia sẻ file này. Có thể tạo mật khẩu bằng `openssl rand -hex 24`. Đặt `PROTOLABEL_HOST_WORKSPACE` thành thư mục cha chứa `data/` và các dataset được phép truy cập.

### Chạy backend và frontend bằng script

```bash
chmod +x scripts/run_all.sh
./scripts/run_all.sh
```

Script tự đọc `.env`, dùng conda environment `sgdetr` nếu có, chạy backend nội bộ tại `127.0.0.1:8100`, frontend tại `0.0.0.0:8101`, và ghi log vào `logs/`. Mở `http://<IP-SERVER>:8101`; trình duyệt sẽ yêu cầu username/password từ `.env`. Nhấn `Ctrl+C` để dừng cả hai service.

Nếu Ubuntu bật UFW, chỉ mở frontend:

```bash
sudo ufw allow 8101/tcp
```

Không mở cổng `8100` ra LAN.

## Import video và tách frame

Trong màn hình khởi đầu, bạn có thể nhập đường dẫn video trên **server** hoặc chọn video từ máy đang mở trình duyệt. Sau đó chọn số frame muốn lấy:

- `300`: lấy đều 300 frame trên toàn video.
- `0`: lấy toàn bộ frame.
- Job chạy nền, xong sẽ tự mở thành một labeling project.

Video theo đường dẫn phải nằm trong `PROTOLABEL_HOST_WORKSPACE`. Video chọn từ máy LAN sẽ được upload vào server trước khi tách frame. Với video rất dài, nên bắt đầu bằng 300–2.000 frame để review nhanh rồi tăng dần.

## Workflow nhanh

1. Nhập đường dẫn thư mục ảnh và tạo project.
2. Chọn checkpoint, chỉnh `conf`, bấm **Prelabel**.
3. Box xanh dương là model sinh; box xanh lá là box thủ công.
4. Sửa box trên canvas hoặc inspector, bấm S để lưu.
5. Dùng bộ lọc `Cần review` để kiểm tra prelabel trước khi export.

Phím tắt: `A/D` ảnh trước/sau, `B` vẽ bbox, `Delete` xoá box, `1–9` đổi class, `P` prelabel ảnh hiện tại, `S` lưu & duyệt.

## GPU prelabel

Backend tự chọn `cuda:0` nếu `torch.cuda.is_available()` trả về true. Khi có GPU,
Ultralytics chạy prelabel bằng CUDA + FP16; toolbar hiển thị chip `GPU · ...` và
job hiển thị device thực tế. Kiểm tra trực tiếp:

```bash
conda activate sgdetr
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

Ép thiết bị cụ thể nếu cần:

```bash
PROTOLABEL_DEVICE=0 ./scripts/run_all.sh
# CPU fallback có chủ đích:
PROTOLABEL_DEVICE=cpu ./scripts/run_all.sh
```

Đọc file video/JPEG ban đầu vẫn do FFmpeg/OpenCV trên CPU; tensor preprocessing và
inference model chạy trên GPU.

## Model registry

ProtoLabel chỉ hiển thị họ YOLO26: `yolo26n`, `yolo26s`, `yolo26m`, `yolo26l`, `yolo26x`. Checkpoint không được lưu trong Git vì có dung lượng lớn. Sau khi clone repository và cài dependency backend, tải đủ model chính thức của Ultralytics bằng:

```bash
cd /duong/dan/toi/ProtoLabel_Tool
conda activate sgdetr
python scripts/download_models.py
```

Script tự tạo `models/`, bỏ qua file đã có và tải các checkpoint còn thiếu. Dùng `python scripts/download_models.py --force` nếu cần tải lại toàn bộ. Có thể dùng thư mục khác với `--model-dir /duong/dan/models` và đặt `PROTOLABEL_MODEL_DIR` trùng với đường dẫn đó khi chạy backend.

Sau khi tải xong, checkpoint nằm trong `Protolabel/models` và sẵn sàng cho prelabel. Model được backend nạp lazy và cache một lần.

## Dữ liệu và license

Annotation lưu trong `<PROTOLABEL_HOST_WORKSPACE>/data/prot0label.sqlite3` (SQLite WAL), ảnh gốc không bị copy. Đây là code mới của project này; không dùng code hoặc dịch vụ thương mại của Roboflow/CVAT. Kiểm tra license riêng của từng checkpoint, thư viện và dataset trước khi phân phối.

## Icon assets

ProtoLabel dùng animated GIF local từ Flaticon cho folder, upload, video và loading. Attribution hiển thị trực tiếp ở góc dưới app; nguồn asset: [Flaticon animated icons](https://www.flaticon.com/animated-icons).


## Chạy bằng Docker Compose

Docker package gồm backend FastAPI/Ultralytics và frontend production Nginx. Backend chạy non-root, chỉ nằm trong network nội bộ của Compose và không publish cổng `8100`; người dùng chỉ truy cập cổng `8101` qua Nginx.

### Build và khởi động

```bash
cd /duong/dan/toi/ProtoLabel_Tool
cp -n .env.example .env
```

Trước khi chạy, sửa `.env`:

- `PROTOLABEL_HOST_WORKSPACE`: đường dẫn tuyệt đối tới workspace chứa `data/` và dataset.
- `PROTOLABEL_AUTH_PASSWORD`: mật khẩu dài; có thể tạo bằng `openssl rand -hex 24`.
- `PROTOLABEL_UID` và `PROTOLABEL_GID`: kết quả của `id -u` và `id -g`, để backend không chạy root nhưng vẫn ghi được database.

Sau đó:

```bash
python scripts/download_models.py
sudo docker compose build
sudo docker compose up -d
sudo docker compose ps
```

Backend phải có trạng thái `healthy`. Mở `http://<IP-SERVER>:8101` và đăng nhập bằng `PROTOLABEL_AUTH_USERNAME`/`PROTOLABEL_AUTH_PASSWORD`. `/docs`, API, media và export đều được bảo vệ bằng cùng credential.

### Xem log và rebuild

```bash
sudo docker compose logs -f
sudo docker compose logs --tail=200 backend

# Chỉ thay frontend
sudo docker compose build frontend
sudo docker compose up -d --force-recreate frontend

# Chỉ thay backend
sudo docker compose build backend
sudo docker compose up -d --force-recreate backend

# Rebuild toàn bộ
sudo docker compose build
sudo docker compose up -d --force-recreate
```

Dừng bằng `sudo docker compose down`. Compose mặc định yêu cầu NVIDIA Container Toolkit vì backend khai báo `gpus: all`. Nếu chỉ chạy CPU, bỏ cấu hình GPU và đặt `PROTOLABEL_DEVICE: cpu` cho backend. Không dùng `docker compose down -v` nếu chưa kiểm tra dữ liệu cần giữ.
