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

Mở `.env` và đặt `PROTOLABEL_HOST_WORKSPACE` thành thư mục cha chứa `data/` và các dataset được phép truy cập. Session mặc định sống 7 ngày; chỉnh `PROTOLABEL_SESSION_TTL_SECONDS` nếu cần. Giới hạn scan mặc định là 250.000 ảnh (`PROTOLABEL_MAX_SCAN_IMAGES`); vượt ngưỡng job sẽ dừng với lỗi rõ ràng thay vì nạp không giới hạn vào RAM.

### Chạy backend và frontend bằng script

```bash
chmod +x scripts/run_all.sh
./scripts/run_all.sh
```

Script tự đọc `.env`, dùng conda environment `sgdetr` nếu có, chạy backend nội bộ tại `127.0.0.1:8100`, frontend tại `0.0.0.0:8101`, và ghi log vào `logs/`. Mở `http://<IP-SERVER>:8101`; trình duyệt sẽ hiện màn Login/Register. Nhấn `Ctrl+C` để dừng cả hai service.

Nếu Ubuntu bật UFW, chỉ mở frontend:

```bash
sudo ufw allow 8101/tcp
```

Không mở cổng `8100` ra LAN.

## Tài khoản và hiệu suất

Khi database chưa có user, backend tự tạo tài khoản quản trị ban đầu:

```text
username: admin
password: admin
```

Lần đăng nhập đầu bắt buộc đổi sang mật khẩu tối thiểu 10 ký tự. Không tiếp tục dùng `admin/admin`. Người dùng khác đăng ký từ màn Register và mặc định có role `annotator`.

Admin mở **Dashboard** trên header để:

- Xem số ảnh distinct có thay đổi annotation, snapshot bbox mới nhất, số lần/ảnh prelabel, active time và last active của từng user. Save lặp lại mà không có thay đổi không được tính.
- Lọc Dashboard theo project và 7 ngày, 30 ngày hoặc toàn bộ thời gian.
- Đổi role `admin`/`annotator`, khóa hoặc mở tài khoản.
- Reset mật khẩu; user bị reset phải đổi mật khẩu ở lần đăng nhập kế tiếp.
- Bật hoặc tắt đăng ký tài khoản mới.

User có thể đổi mật khẩu từ nút **Password** trên header. Password chỉ được lưu dưới dạng `scrypt` hash; phiên đăng nhập dùng cookie HttpOnly/SameSite. Hiệu suất bắt đầu được ghi từ lúc deploy phiên bản account, annotation cũ không bị sửa và không được gán ngược cho user.

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

Phím tắt: `A/D` ảnh trước/sau, `W` vẽ bbox, `Delete` xoá box, `1–9` đổi class, `P` prelabel ảnh hiện tại, `Ctrl+S` lưu & duyệt, `Z`/`X` chọn zoom in/out rồi click vị trí cần zoom; khi đã zoom có thể kéo ảnh để pan.

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

Danh sách Prelabel không hardcode model. Backend quét trực tiếp thư mục `models/` mỗi lần gọi `/api/models` và hiển thị các file `.pt`, `.onnx`, `.engine`, `.torchscript`. Thêm checkpoint mới vào thư mục này rồi refresh model/page; không cần sửa backend.

Checkpoint không lưu trong Git vì dung lượng lớn. Tải bộ YOLO26 mặc định của Ultralytics bằng:

```bash
cd /duong/dan/toi/ProtoLabel_Tool
conda activate sgdetr
python scripts/download_models.py
```

Script tự tạo `models/`, bỏ qua file đã có; dùng `--force` để tải lại. Model tùy chỉnh phải tương thích với Ultralytics `YOLO(...)`, nếu không inference sẽ trả lỗi model tương ứng.

## Dữ liệu và license

Annotation lưu trong `<PROTOLABEL_HOST_WORKSPACE>/data/prot0label.sqlite3` (SQLite WAL), ảnh gốc không bị copy. Đây là code mới của project này; không dùng code hoặc dịch vụ thương mại của Roboflow/CVAT. Kiểm tra license riêng của từng checkpoint, thư viện và dataset trước khi phân phối.

Mọi tọa độ ảnh (bbox, keypoints, crop bbox và tâm mask) dùng số thực chuẩn hóa `0..1`, gốc ở góc trên-trái. Bbox có dạng `[x, y, w, h]`, trong đó `x, y` là góc trên-trái và `w, h` là kích thước hộp, đều chia cho chiều rộng/chiều cao khung hình. Bên tiêu thụ đổi sang pixel bằng cách nhân trục x với `frame_w` và trục y với `frame_h`.

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
- `PROTOLABEL_UID` và `PROTOLABEL_GID`: kết quả của `id -u` và `id -g`, để backend không chạy root nhưng vẫn ghi được database.

Sau đó:

```bash
python scripts/download_models.py
sudo docker compose build
sudo docker compose up -d
sudo docker compose ps
```

Backend phải có trạng thái `healthy`. Mở `http://<IP-SERVER>:8101`; màn Login/Register sẽ xuất hiện. Đăng nhập lần đầu bằng `admin/admin` và đổi password ngay. `/docs`, API, media và export đều yêu cầu session hợp lệ.

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
