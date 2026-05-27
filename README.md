# Hệ thống Chat P2P

Hệ thống chat P2P gồm một registry bootstrap và các peer triển khai bằng asyncio.

## Tính năng
- Máy chủ bootstrap để khám phá các peer
- Các node peer với kết nối TCP hai chiều
- Chat trực tiếp và theo nhóm với ACK và cơ chế retry
- Heartbeat và phát hiện trạng thái offline
- Trao đổi peer khi bootstrap không khả dụng
- CLI với các lệnh hỗ trợ

## Cài đặt

```bash
pip install -r requirements.txt
```

## Chạy

Khởi động bootstrap server:

```bash
python -m bootstrap run --host 0.0.0.0 --port 9000
```

Khởi động peer:

```bash
python -m peer run --peer-id peer_a --host 0.0.0.0 --port 5001 --advertise-host <your_device_ip> --bootstrap-host <bootstrap_server_ip> --web-host 0.0.0.0 --web-port 8081
```

Ghi chú:
- Trường `advertise_host` cần phải truy cập được từ các peer khác (LAN hoặc IP công khai). Nếu không cung cấp, peer sẽ thử tự phát hiện thông qua bootstrap/seed.
- Nếu sử dụng file `.env`, đặt `P2P_ADVERTISE_HOST` và `P2P_BOOTSTRAP_HOST` tương ứng cho từng máy.


## Giao diện Web (Web UI)

Mở giao diện peer trên trình duyệt:

```
http://<web_host>:<web_port>
```

## Cấu hình

Ví dụ file cấu hình tùy chọn (JSON hoặc YAML):

```json
{
  "peer_id": "peer_a",
  "host": "0.0.0.0",
  "advertise_host": "127.0.0.1",
  "port": 5001,
  "bootstrap_host": "127.0.0.1",
  "bootstrap_port": 9000,
  "heartbeat_interval": 5,
  "heartbeat_timeout": 15,
  "connect_limit": 50,
  "seeds": ["127.0.0.1:5002"],
  "web_host": "0.0.0.0",
  "web_port": 8081,
  "log_level": "INFO",
  "log_file": "logs/peer_a.log",
  "log_format": "text"
}
```

Chạy với file cấu hình:

```bash
python -m peer run --config config.json
```

Ví dụ profile (config.json):

```json
{
  "host": "0.0.0.0",
  "profiles": {
    "lab": {"bootstrap_host": "192.168.1.10"}
  }
}
```

```bash
python -m peer run --config config.json --profile lab
```