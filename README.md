[English](#ghostnode) | [فارسی](#ghostnode-1)

---

# GhostNode

A self-hosted proxy server panel built from scratch. Manage inbounds, clients, routing, and outbounds through a clean web UI or REST API.

## Features

- Multi-inbound support (each inbound has its own port and transport)
- Multi-client management with per-client traffic limits and expiry dates
- GNP binary protocol with NanoID-20 client IDs and yamux-style multiplexing
- Routing engine with domain, IP, CIDR, port, protocol, and inbound tag matching
- GeoIP and GeoSite support via v2fly `geoip.dat` and `geosite.dat`
- Six transport types: websocket, http2, grpc, http-request, http-request-sse, http-request-body
- Outbound types: direct, block, ghostnode, socks5, http
- Per-client traffic accounting (upload + download byte counters)
- Dark and light panel themes
- Persian and English UI
- URI-path authentication (no login page, panel hidden behind a secret path)
- `gn://` config link generation with QR code export
- Optional Xray core integration with its own inbounds, clients, and outbounds

## Quick Install

Run as root on a fresh Debian/Ubuntu server:

```bash
bash <(curl -fsSL https://github.com/FrenchToblerone54/GhostNode/releases/latest/download/install.sh)
```

## Manual Run

```bash
python3.13 main.py -c /etc/ghostnode/config.toml
```

Generate a random panel path token:
```bash
python3.13 main.py --generate-token
```

## Config File

The config is TOML. Default path: `/etc/ghostnode/config.toml`

```toml
[panel]
host = "127.0.0.1"
port = 9090
path = "yourSecretPath"
threads = 4

[database]
path = "/etc/ghostnode/ghostnode.db"

[geo]
geoip_dat_path = "/etc/ghostnode/geo/geoip.dat"
geosite_path = "/etc/ghostnode/geo/geosite.dat"

[logging]
level = "info"
file = "/var/log/ghostnode.log"

[server]
hostname = ""
auto_update = true
update_check_interval = 300
update_proxy = ""
```

`path` acts as the URI auth token. The panel is only reachable at `/{path}/`. All other paths return 404.

## Config Link Format

Clients connect using a `gn://` URI:

```
gn://NANOID@host:port?transport=ws&path=/gn&security=tls&sni=override.com&fp=BASE64#Name
```

| Parameter | Description |
|-----------|-------------|
| `NANOID` | 20-character client NanoID |
| `host:port` | Inbound address |
| `transport` | Transport type |
| `path` | HTTP path used by the transport |
| `security` | `tls` or omitted |
| `sni` | Optional TLS SNI override |
| `fp` | Server public key SHA-256 fingerprint (base64url, MITM protection) |
| `#Name` | Human-readable client label |

## Transport Types

| Type | Key | Notes |
|------|-----|-------|
| WebSocket | `ws` | Standard WebSocket upgrade |
| HTTP/2 | `h2` | Full-duplex H2 streams |
| gRPC | `grpc` | gRPC streaming |
| HTTP Request | `http-request` | Long-poll HTTP GET/POST |
| HTTP Request SSE | `http-request-sse` | Server-Sent Events downlink |
| HTTP Request Body | `http-request-body` | Chunked request body |

## GeoIP and GeoSite

GhostNode uses the v2fly binary format (`geoip.dat`, `geosite.dat`). The install script downloads them automatically. To update manually:

```bash
curl -Lo /etc/ghostnode/geo/geoip.dat https://github.com/v2fly/geoip/releases/latest/download/geoip.dat
curl -Lo /etc/ghostnode/geo/geosite.dat https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat
```

## Documentation

- [REST API Reference](docs/api.md)

## Client Library

`ghostnode-client` can be embedded in other Python apps as a backend:

```python
from client.api import GhostNodeClient, parse_gn_link

cfg = parse_gn_link("gn://...")

# Start a SOCKS5 proxy
async with GhostNodeClient(cfg) as client:
    await client.start_socks5("127.0.0.1", 1080)

# Or open a raw tunneled stream directly
async with GhostNodeClient(cfg) as client:
    reader, writer = await client.open_stream("example.com", 443)
```

---

<div dir="rtl">

# GhostNode

یک پنل سرور پروکسی self-hosted که از صفر ساخته شده است. مدیریت inbound‌ها، کلاینت‌ها، routing و outbound‌ها از طریق یک رابط وب تمیز یا REST API.

## ویژگی‌ها

- پشتیبانی از چند inbound (هر inbound پورت و transport مخصوص به خود را دارد)
- مدیریت چند کلاینت با محدودیت ترافیک و تاریخ انقضا به ازای هر کلاینت
- پروتکل باینری GNP با NanoID-20 به عنوان شناسه کلاینت و multiplexing به سبک yamux
- موتور routing با تطبیق domain، IP، CIDR، port، protocol و inbound tag
- پشتیبانی از GeoIP و GeoSite از طریق فایل‌های `geoip.dat` و `geosite.dat` مربوط به v2fly
- شش نوع transport: websocket، http2، grpc، http-request، http-request-sse، http-request-body
- انواع outbound: direct، block، ghostnode، socks5، http
- حسابداری ترافیک به ازای هر کلاینت (شمارنده‌های بایت آپلود + دانلود)
- تم‌های تاریک و روشن برای پنل
- رابط کاربری فارسی و انگلیسی
- احراز هویت مبتنی بر URI-path (بدون صفحه ورود، پنل پشت یک مسیر مخفی پنهان است)
- تولید لینک config با فرمت `gn://` و امکان خروجی QR code
- یکپارچه‌سازی اختیاری با هسته Xray با inbound‌ها، کلاینت‌ها و outbound‌های مخصوص به خود

## نصب سریع

به عنوان root روی یک سرور تازه Debian/Ubuntu اجرا کنید:

<div dir="ltr">

```bash
bash <(curl -fsSL https://github.com/FrenchToblerone54/GhostNode/releases/latest/download/install.sh)
```

</div>

## اجرای دستی

<div dir="ltr">

```bash
python3.13 main.py -c /etc/ghostnode/config.toml
```

</div>

تولید یک token تصادفی برای مسیر پنل:

<div dir="ltr">

```bash
python3.13 main.py --generate-token
```

</div>

## فایل پیکربندی

فایل config به فرمت TOML است. مسیر پیش‌فرض: `/etc/ghostnode/config.toml`

<div dir="ltr">

```toml
[panel]
host = "127.0.0.1"
port = 9090
path = "yourSecretPath"
threads = 4

[database]
path = "/etc/ghostnode/ghostnode.db"

[geo]
geoip_dat_path = "/etc/ghostnode/geo/geoip.dat"
geosite_path = "/etc/ghostnode/geo/geosite.dat"

[logging]
level = "info"
file = "/var/log/ghostnode.log"

[server]
hostname = ""
auto_update = true
update_check_interval = 300
update_proxy = ""
```

</div>

`path` به عنوان توکن احراز هویت URI عمل می‌کند. پنل فقط از طریق `/{path}/` قابل دسترسی است. تمام مسیرهای دیگر 404 برمی‌گردانند.

## فرمت لینک Config

کلاینت‌ها با استفاده از یک URI با پیشوند `gn://` متصل می‌شوند:

<div dir="ltr">

```
gn://NANOID@host:port?transport=ws&path=/gn&security=tls&sni=override.com&fp=BASE64#Name
```

</div>

| پارامتر | توضیح |
|---------|-------|
| `NANOID` | NanoID 20 کاراکتری کلاینت |
| `host:port` | آدرس inbound |
| `transport` | نوع transport |
| `path` | مسیر HTTP مورد استفاده توسط transport |
| `security` | `tls` یا حذف شده |
| `sni` | override اختیاری TLS SNI |
| `fp` | اثر انگشت SHA-256 کلید عمومی سرور (base64url، محافظت در برابر MITM) |
| `#Name` | برچسب قابل خواندن برای کلاینت |

## انواع Transport

| نوع | کلید | توضیحات |
|-----|------|---------|
| WebSocket | `ws` | ارتقاء استاندارد WebSocket |
| HTTP/2 | `h2` | استریم‌های H2 full-duplex |
| gRPC | `grpc` | استریمینگ gRPC |
| HTTP Request | `http-request` | HTTP GET/POST با long-poll |
| HTTP Request SSE | `http-request-sse` | downlink با Server-Sent Events |
| HTTP Request Body | `http-request-body` | request body به صورت chunked |

## مستندات

- [مرجع REST API](docs/api.md)

## GeoIP و GeoSite

GhostNode از فرمت باینری v2fly استفاده می‌کند (`geoip.dat`، `geosite.dat`). اسکریپت نصب آن‌ها را به صورت خودکار دانلود می‌کند. برای به‌روزرسانی دستی:

<div dir="ltr">

```bash
curl -Lo /etc/ghostnode/geo/geoip.dat https://github.com/v2fly/geoip/releases/latest/download/geoip.dat
curl -Lo /etc/ghostnode/geo/geosite.dat https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat
```

</div>

## کتابخانه کلاینت

`ghostnode-client` می‌تواند به عنوان یک backend در سایر اپلیکیشن‌های Python استفاده شود:

<div dir="ltr">

```python
from client.api import GhostNodeClient, parse_gn_link

cfg = parse_gn_link("gn://...")

# راه‌اندازی پروکسی SOCKS5
async with GhostNodeClient(cfg) as client:
    await client.start_socks5("127.0.0.1", 1080)

# یا باز کردن مستقیم یک استریم تانل‌شده
async with GhostNodeClient(cfg) as client:
    reader, writer = await client.open_stream("example.com", 443)
```

</div>

</div>
