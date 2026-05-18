[English](README.md)

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

## GeoIP و GeoSite

GhostNode از فرمت باینری v2fly استفاده می‌کند (`geoip.dat`، `geosite.dat`). اسکریپت نصب آن‌ها را به صورت خودکار دانلود می‌کند. برای به‌روزرسانی دستی:

<div dir="ltr">

```bash
curl -Lo /etc/ghostnode/geo/geoip.dat https://github.com/v2fly/geoip/releases/latest/download/geoip.dat
curl -Lo /etc/ghostnode/geo/geosite.dat https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat
```

</div>

## مستندات

- [مرجع REST API](docs/api.md)

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
