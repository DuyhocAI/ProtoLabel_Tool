# ProtoLabel

Ứng dụng labeling self-hosted, độc lập với CVAT/Roboflow. Dùng cho ảnh và video: quét thư mục, prelabel bbox/segment/skeleton/3D bằng checkpoint local, chỉnh annotation bằng chuột hoặc bàn phím và export/import JSON không mất dữ liệu.

[🇬🇧 English documentation](README.en.md) · [Contributing](CONTRIBUTING.md) · [License](LICENSE)

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

Đăng nhập sai bị giới hạn mặc định 5 lần trong 15 phút theo username + IP. Có thể chỉnh bằng `PROTOLABEL_LOGIN_MAX_ATTEMPTS` và `PROTOLABEL_LOGIN_WINDOW_SECONDS`.

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
2. Chọn BBox, Segment, Skeleton hoặc 3D trên thanh công cụ.
3. Nhập lớp cần tìm, ví dụ `person`, chọn checkpoint tương thích và bấm **Prelabel trang này** hoặc **Prelabel toàn project**.
4. Prelabel chỉ cập nhật prediction AI cùng loại và cùng lớp được yêu cầu. Annotation thủ công và lớp khác đã có (ví dụ `car`) được giữ nguyên.
5. Annotation AI dùng viền cam nét đứt; annotation thủ công dùng viền xanh liền.
6. Sửa trên canvas hoặc inspector, bấm `Ctrl+S` để lưu và duyệt.
7. Dùng **Export JSON** để giữ đầy đủ segment/skeleton/3D và **Import JSON** để merge trở lại server ở phiên sau. Export YOLO chỉ dành cho bbox.

Phím tắt mặc định: `A/D` ảnh trước/sau, `W/G/K/C` chọn bbox/segment/skeleton/3D, `Q` đóng Edit annotation, `Shift+W` tạo annotation mặc định giữa ảnh, phím mũi tên dịch annotation đã chọn 1 px (`Shift` + mũi tên: 10 px), `Backspace/Delete` xoá, `1–9` đổi class, `P` prelabel ảnh hiện tại, `Ctrl+S` lưu, `Z/X` chọn zoom in/out rồi click vị trí cần zoom. Mỗi user có thể đổi shortcut trong màn hình cài đặt; ô ghi shortcut nhận trực tiếp `Ctrl`, `Shift`, `Alt`, `Meta` và phím chính. Khi tổ hợp đã được gán cho app (ví dụ `Ctrl+A`), app chặn hành vi mặc định của trang và xử lý shortcut trước.

Khi chọn annotation từ canvas hoặc Outliner, editor chuyển sang chế độ cô lập: chỉ annotation đó nhận tương tác. Kéo bốn handle của bbox hoặc từng vertex/keypoint của segment, skeleton và cuboid để chỉnh; bấm `×` tại **Edit annotation** trước khi chọn annotation khác.

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

## Remote CAP/model API

ProtoLabel đọc thêm `remote-models.json` và trộn các service HTTP vào cùng danh sách checkpoint local. Entry `face.detect` mặc định gọi Face Detection Service qua adapter `face-detection-v2`; response được chuẩn hóa thành bbox `face` của ProtoLabel. Nút **Test ảnh** chạy ảnh hiện tại nhưng chỉ tạo preview trong editor; bấm `Ctrl+S` nếu muốn lưu. Các nút prelabel trang/toàn project dùng cùng background job như model local.

Mỗi service khai `id`, `label`, `capability`, `provider`, `adapter`, `endpoint`, `tasks`, timeout và class đầu ra. Endpoint và token bí mật chỉ cấu hình phía server; frontend chỉ nhận metadata an toàn từ `/api/models`. Hiện adapter triển khai là `face-detection-v2`; CAP có schema khác cần thêm adapter dịch request/response tương ứng, không gọi thẳng provider từ frontend.

## Model registry

Danh sách Prelabel không hardcode model. Backend quét trực tiếp thư mục `models/` mỗi lần gọi `/api/models` và hiển thị các file `.pt`, `.onnx`, `.engine`, `.torchscript`. Thêm checkpoint mới vào thư mục này rồi refresh model/page; không cần sửa backend.

Checkpoint không lưu trong Git vì dung lượng lớn. Tải bộ YOLO26 mặc định của Ultralytics bằng:

```bash
cd /duong/dan/toi/ProtoLabel_Tool
conda activate sgdetr
python scripts/download_models.py
```

Script tự tạo `models/`, bỏ qua file đã có; dùng `--force` để tải lại. Bộ mặc định gồm YOLOE segment hỗ trợ text prompt, YOLO26 pose và YOLO26 depth. Model tùy chỉnh phải tương thích với Ultralytics, nếu không inference sẽ trả lỗi model tương ứng.

- **Segment:** YOLOE nhận danh sách lớp dạng text và trả instance polygon.
- **Skeleton:** pose checkpoint trả 17 keypoint COCO; lớp nhập vào dùng để lọc kết quả của checkpoint.
- **3D:** detector tạo 8 điểm cuboid chiếu trên ảnh, depth checkpoint bổ sung độ sâu ước lượng. Đây không phải cuboid 3D đã hiệu chuẩn theo camera; muốn tọa độ thế giới chính xác cần camera intrinsics/extrinsics và model 3D chuyên dụng.

## Dữ liệu và license

Annotation, project, user và lịch sử lưu trong `<PROTOLABEL_HOST_WORKSPACE>/data/prot0label.sqlite3` (SQLite WAL). Ảnh quét từ server không bị copy; ảnh/video upload từ trình duyệt nằm dưới thư mục `data/`. Rebuild image/container không làm mất dữ liệu vì `/workspace` là bind mount; không dùng `docker compose down -v` hoặc xóa workspace khi chưa kiểm tra backup.

Backend tạo SQLite online backup lúc khởi động và mỗi 24 giờ, giữ 14 ngày mặc định. Docker lưu các bản này vào `PROTOLABEL_HOST_BACKUP_DIR`, tách khỏi volume workspace. Nên đặt thư mục đó trên ổ/NAS khác và đồng bộ offsite; bản sao trên cùng ổ đĩa không bảo vệ được khi hỏng ổ. `PRAGMA optimize` cũng chạy cùng lịch bảo trì.

SQLite phù hợp một instance và team nhỏ. Cấu hình hiện tại được kiểm thử chức năng nhưng chưa load-test để cam kết một con số concurrent user; nên theo dõi `database is locked` và độ trễ save. Nếu cần nhiều backend replica hoặc tải ghi đồng thời lớn, phải chuyển sang PostgreSQL thay vì tăng worker tùy ý.

Đây là code mới của project này; không dùng code hoặc dịch vụ thương mại của Roboflow/CVAT. Kiểm tra license riêng của từng checkpoint, thư viện và dataset trước khi phân phối. **Lưu ý về license phụ thuộc:** backend phụ thuộc trực tiếp vào [Ultralytics](https://github.com/ultralytics/ultralytics), phát hành theo AGPL-3.0 (hoặc license thương mại Ultralytics Enterprise) — xem [LICENSE](LICENSE) để biết license của chính ProtoLabel được chọn tương thích với điều đó thế nào.

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
- `PROTOLABEL_HOST_BACKUP_DIR`: thư mục backup riêng, đã tồn tại và user chạy Docker có quyền ghi.
- `PROTOLABEL_UID` và `PROTOLABEL_GID`: kết quả của `id -u` và `id -g`, để backend không chạy root nhưng vẫn ghi được database.

Sau đó:

```bash
mkdir -p models backups
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
