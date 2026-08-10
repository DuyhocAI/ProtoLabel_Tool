# ProtoLabel

Ứng dụng labeling self-hosted, tách khỏi SafeGuard/CVAT/Roboflow. Dùng cho ảnh và video đã tách frame: quét thư mục ảnh, prelabel bằng checkpoint local, sửa bbox bằng keyboard và export YOLO/JSON.

## Chạy không Docker

Không chạy đồng thời cách này với Docker Compose vì cả hai cùng dùng cổng `8100` và `8101`.

### Cài dependency và kiểm tra build lần đầu

```bash
cd /home/tts02/AI/DuyNAB/Protolabel
conda activate sgdetr
python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
```

Lệnh build ở trên kiểm tra và tạo frontend production build. Để chạy ứng dụng không Docker, dùng script bên dưới.

### Chạy backend và frontend bằng script

```bash
cd /home/tts02/AI/DuyNAB/Protolabel
conda activate sgdetr
chmod +x scripts/run_all.sh
./scripts/run_all.sh
```

Script tự dùng conda environment `sgdetr`, tự cài npm dependencies nếu thiếu, chạy backend tại cổng `8100`, frontend tại cổng `8101`, và ghi log vào `logs/backend.log` cùng `logs/frontend.log`. Nhấn `Ctrl+C` để dừng cả hai service.

Mở `http://<IP-SERVER>:8101`. Không nhập `0.0.0.0` vào trình duyệt; đó chỉ là địa chỉ bind. Backend chỉ cho phép chọn thư mục bên trong `/home/tts02/AI/DuyNAB`; đổi bằng biến `PROTOLABEL_ROOT` nếu cần.

Nếu máy khác trong LAN không truy cập được, kiểm tra firewall trên server:

```bash
sudo ufw allow 8101/tcp
# Chỉ mở 8100 nếu máy khác gọi trực tiếp API.
sudo ufw allow 8100/tcp
```

## Import video và tách frame

Trong màn hình khởi đầu, bạn có thể nhập đường dẫn video trên **server** hoặc chọn video từ máy đang mở trình duyệt. Sau đó chọn số frame muốn lấy:

- `300`: lấy đều 300 frame trên toàn video.
- `0`: lấy toàn bộ frame.
- Job chạy nền, xong sẽ tự mở thành một labeling project.

Video theo đường dẫn phải nằm trong thư mục được phép (mặc định `/home/tts02/AI/DuyNAB`). Video chọn từ máy LAN sẽ được upload vào server trước khi tách frame. Với video rất dài, nên bắt đầu bằng 300–2.000 frame để review nhanh rồi tăng dần.

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

ProtoLabel chỉ hiển thị họ YOLO26: `yolo26n`, `yolo26s`, `yolo26m`, `yolo26l`, `yolo26x`. Toàn bộ checkpoint chính thức của Ultralytics được đặt tập trung trong `Protolabel/models` và đều sẵn sàng sử dụng. Có thể thêm model bằng cách sửa `model_options()` trong `backend/app/main.py`. Model được nạp lazy và cache một lần.

## Dữ liệu và license

Annotation lưu trong `/home/tts02/AI/DuyNAB/data/prot0label.sqlite3` (SQLite WAL), ảnh gốc không bị copy. Đây là code mới của project này; không dùng code hoặc dịch vụ thương mại của Roboflow/CVAT. Kiểm tra license riêng của từng checkpoint, thư viện và dataset trước khi phân phối.

## Icon assets

ProtoLabel dùng animated GIF local từ Flaticon cho folder, upload, video và loading. Attribution hiển thị trực tiếp ở góc dưới app; nguồn asset: [Flaticon animated icons](https://www.flaticon.com/animated-icons).


## Chạy bằng Docker Compose

Docker package gồm backend FastAPI có Ultralytics/PyTorch và frontend production Nginx. Năm checkpoint YOLO26 chính thức nằm trong `models/`. Không chạy `scripts/run_all.sh` cùng lúc với Compose vì trùng cổng.

### Build và khởi động

```bash
cd /home/tts02/AI/DuyNAB/Protolabel
cp -n .env.example .env
sudo docker compose build
sudo docker compose up -d
sudo docker compose ps
```

Backend phải có trạng thái `healthy`. Mở `http://<IP-SERVER>:8101`; backend được proxy qua frontend, còn cổng `8100` chỉ cần khi gọi API trực tiếp.

### Xem log và xử lý lỗi

```bash
sudo docker compose logs -f
sudo docker compose logs --tail=200 backend
```

Sau khi sửa source, rebuild service liên quan:

```bash
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

Dừng và gỡ container/network của Compose:

```bash
sudo docker compose down
```

Compose mặc định yêu cầu NVIDIA Container Toolkit vì backend khai báo `gpus: all`. Nếu chỉ chạy CPU, bỏ cấu hình GPU và đặt `PROTOLABEL_DEVICE: cpu` cho backend. Không dùng `docker compose down -v` nếu chưa kiểm tra dữ liệu cần giữ.
