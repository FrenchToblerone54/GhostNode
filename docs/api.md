# GhostNode REST API Reference

All endpoints are served under the panel prefix. Replace `{panel_path}` with the value of `path` from your config file.

Base path: `/{panel_path}/api/`

Authentication is implicit: any request whose path does not start with `/{panel_path}` returns `404`. All request and response bodies use `Content-Type: application/json` unless noted otherwise.

---

## Inbounds

### List inbounds

`GET /{panel_path}/api/inbounds`

Returns all configured inbounds.

**Response**

```json
[
  {
    "id": 1,
    "tag": "inbound-ws",
    "port": 443,
    "transport": "websocket",
    "path": "/gn",
    "ssl_cert": "",
    "ssl_key": "",
    "listen_ip": "0.0.0.0",
    "enabled": 1,
    "ext_host": "",
    "ext_port": 0,
    "ext_tls": 0,
    "host": "",
    "sni": ""
  }
]
```

---

### Create inbound

`POST /{panel_path}/api/inbounds`

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | yes | Unique identifier tag for this inbound |
| `port` | integer | yes | Port to listen on |
| `transport` | string | no | One of: `websocket`, `http2`, `grpc`, `http-request`, `http-request-sse`, `http-request-body`. Default: `websocket` |
| `path` | string | no | HTTP path for the transport. Default: `/gn` |
| `ssl_cert` | string | no | Path to TLS certificate file |
| `ssl_key` | string | no | Path to TLS private key file |
| `listen_ip` | string | no | IP address to bind. Default: `0.0.0.0` |

**Response** `201 Created`

```json
{"id": 1}
```

---

### Update inbound

`PUT /{panel_path}/api/inbounds/{id}`

Restarts the inbound after updating. All fields are optional (partial update).

**Request body**

| Field | Type | Description |
|-------|------|-------------|
| `tag` | string | Inbound tag |
| `port` | integer | Listening port |
| `transport` | string | Transport type |
| `path` | string | HTTP path |
| `ssl_cert` | string | TLS certificate file path |
| `ssl_key` | string | TLS private key file path |
| `enabled` | integer | `1` to enable, `0` to disable |
| `ext_host` | string | External hostname override for config link generation |
| `ext_port` | integer | External port override for config link generation |
| `ext_tls` | integer | `1` if the external-facing connection uses TLS |
| `host` | string | Host header value |
| `sni` | string | TLS SNI override |
| `listen_ip` | string | Bind IP address |

**Response**

```json
{"ok": true}
```

---

### Delete inbound

`DELETE /{panel_path}/api/inbounds/{id}`

Stops and removes the inbound.

**Response**

```json
{"ok": true}
```

---

### Bulk inbound actions

`POST /{panel_path}/api/inbounds/bulk`

**Request body**

| Field | Type | Description |
|-------|------|-------------|
| `action` | string | One of: `enable`, `disable`, `delete` |
| `ids` | array of integer | Inbound IDs to act on |

```json
{"action": "disable", "ids": [1, 2, 3]}
```

**Response**

```json
{"ok": true}
```

---

## Clients

### List clients

`GET /{panel_path}/api/clients`

Returns all clients.

**Response**

```json
[
  {
    "id": 1,
    "nanoid": "AbCdEfGhIjKlMnOpQrSt",
    "name": "Alice",
    "inbound_tag": "inbound-ws",
    "traffic_limit": 10737418240,
    "traffic_up": 1048576,
    "traffic_down": 2097152,
    "expire_date": "2026-01-01",
    "enabled": 1
  }
]
```

---

### Create client

`POST /{panel_path}/api/clients`

A NanoID-20 is automatically generated for the new client.

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Human-readable client label |
| `inbound_tag` | string | yes | Tag of the inbound this client belongs to |
| `traffic_limit_gb` | number | no | Traffic limit in gigabytes. `0` means unlimited |
| `expire_date` | string | no | Expiry date in `YYYY-MM-DD` format. Empty string means no expiry |

**Response** `201 Created`

Full client object (same schema as list response).

---

### Update client

`PUT /{panel_path}/api/clients/{id}`

All fields are optional (partial update). `traffic_limit_gb` is converted to bytes internally and stored as `traffic_limit`.

**Request body**

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Client label |
| `inbound_tag` | string | Inbound tag |
| `traffic_limit_gb` | number | Traffic limit in GB |
| `expire_date` | string | Expiry date (`YYYY-MM-DD`) |
| `enabled` | integer | `1` to enable, `0` to disable |

**Response**

```json
{"ok": true}
```

---

### Delete client

`DELETE /{panel_path}/api/clients/{id}`

**Response**

```json
{"ok": true}
```

---

### Bulk client actions

`POST /{panel_path}/api/clients/bulk`

**Request body**

| Field | Type | Description |
|-------|------|-------------|
| `action` | string | One of: `enable`, `disable`, `delete` |
| `ids` | array of integer | Client IDs to act on |

```json
{"action": "delete", "ids": [4, 5]}
```

**Response**

```json
{"ok": true}
```

---

### Reset client traffic

`POST /{panel_path}/api/clients/{id}/reset`

Resets the upload and download byte counters to zero.

**Response**

```json
{"ok": true}
```

---

### Toggle client enabled state

`POST /{panel_path}/api/clients/{id}/toggle`

Flips the `enabled` flag: enables a disabled client and disables an enabled one.

**Response**

```json
{"ok": true}
```

---

### Get client config link

`POST /{panel_path}/api/clients/{id}/config-link`

Generates a `gn://` URI for the client and a QR code as an inline SVG string.

**Response**

```json
{
  "link": "gn://AbCdEfGhIjKlMnOpQrSt@1.2.3.4:443?transport=ws&path=%2Fgn&security=tls&fp=BASE64#Alice",
  "qr_svg": "<svg ...>...</svg>"
}
```

**Error responses**

| Status | Body |
|--------|------|
| `404` | `{"error": "not found"}` — client ID does not exist |
| `404` | `{"error": "inbound not found"}` — client's inbound tag no longer exists |

---

## Outbounds

### List outbounds

`GET /{panel_path}/api/outbounds`

Returns all outbounds in their stored order.

**Response**

```json
[
  {
    "id": 1,
    "tag": "direct",
    "type": "direct",
    "config": "{}"
  }
]
```

---

### Create outbound

`POST /{panel_path}/api/outbounds`

Triggers a router reload after creation.

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tag` | string | yes | Unique identifier tag |
| `type` | string | no | One of: `direct`, `block`, `ghostnode`, `socks5`, `http`. Default: `direct` |
| `config` | object or string | no | Type-specific configuration as a JSON object or JSON string |

**Config shapes by type**

| Type | Config fields |
|------|--------------|
| `direct` | (empty) |
| `block` | (empty) |
| `ghostnode` | `url`, `nanoid`, `transport`, `path`, `sni`, `host`, `allow_insecure` |
| `socks5` | `host`, `port`, `username`, `password` |
| `http` | `host`, `port`, `username`, `password` |

**Response** `201 Created`

```json
{"id": 1}
```

---

### Update outbound

`PUT /{panel_path}/api/outbounds/{id}`

Triggers a router reload after update. All fields are optional (partial update). If `config` is provided as an object it is serialized to a JSON string.

**Request body**

| Field | Type | Description |
|-------|------|-------------|
| `tag` | string | Outbound tag |
| `type` | string | Outbound type |
| `config` | object or string | Type-specific config |

**Response**

```json
{"ok": true}
```

---

### Delete outbound

`DELETE /{panel_path}/api/outbounds/{id}`

Triggers a router reload after deletion.

**Response**

```json
{"ok": true}
```

---

### Reorder outbounds

`POST /{panel_path}/api/outbounds/reorder`

Sets the display/evaluation order of outbounds. Triggers a router reload.

**Request body**

Array of outbound IDs in the desired order:

```json
[3, 1, 2]
```

**Response**

```json
{"ok": true}
```

---

## Routing Rules

### List routing rules

`GET /{panel_path}/api/routing`

Returns all routing rules. The `domain` and `ip` fields are returned as arrays.

**Response**

```json
[
  {
    "id": 1,
    "outbound_tag": "direct",
    "domain": ["geosite:cn"],
    "ip": ["geoip:cn"],
    "port": "",
    "protocol": "",
    "inbound_tag": "",
    "ord": 0
  }
]
```

---

### Create routing rule

`POST /{panel_path}/api/routing`

Triggers a router reload after creation.

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `outbound_tag` | string | yes | Tag of the outbound to route matched traffic to |
| `domain` | array or string | no | Domain patterns. Supports `geosite:` prefixes |
| `ip` | array or string | no | IP/CIDR patterns. Supports `geoip:` prefixes |
| `port` | string | no | Port or range to match (e.g. `"80"`, `"443,8080"`, `"1000-2000"`) |
| `protocol` | string | no | Protocol to match (e.g. `"tcp"`, `"udp"`) |
| `inbound_tag` | string | no | Restrict rule to traffic arriving on this inbound tag |
| `ord` | integer | no | Sort order index. Lower values are evaluated first |

**Response** `201 Created`

```json
{"id": 1}
```

---

### Update routing rule

`PUT /{panel_path}/api/routing/{id}`

Triggers a router reload after update. All fields are optional (partial update). Array values for `domain` and `ip` are serialized to JSON strings internally.

**Request body**

| Field | Type | Description |
|-------|------|-------------|
| `outbound_tag` | string | Target outbound tag |
| `domain` | array or string | Domain match patterns |
| `ip` | array or string | IP/CIDR match patterns |
| `port` | string | Port or port range |
| `protocol` | string | Protocol filter |
| `inbound_tag` | string | Inbound tag filter |
| `ord` | integer | Sort order index |

**Response**

```json
{"ok": true}
```

---

### Delete routing rule

`DELETE /{panel_path}/api/routing/{id}`

Triggers a router reload after deletion.

**Response**

```json
{"ok": true}
```

---

### Reorder routing rules

`POST /{panel_path}/api/routing/reorder`

Sets the evaluation order of routing rules. Triggers a router reload.

**Request body**

Array of rule IDs in the desired order:

```json
[2, 1, 3]
```

**Response**

```json
{"ok": true}
```

---

## Stats

### Get system stats

`GET /{panel_path}/api/stats`

Returns system resource usage and the running GhostNode version. Network `net_sent` and `net_recv` are measured over a ~0.5 second sample window and extrapolated to bytes/sec.

**Response**

```json
{
  "version": "0.18.31",
  "cpu_percent": 4.2,
  "cpu_count": 4,
  "ram_used": 512000000,
  "ram_total": 2048000000,
  "ram_percent": 25.0,
  "swap_used": 0,
  "swap_total": 1024000000,
  "swap_percent": 0.0,
  "disk_used": 8000000000,
  "disk_total": 50000000000,
  "disk_percent": 16.0,
  "net_sent": 204800,
  "net_recv": 819200,
  "load_1": 0.12,
  "load_5": 0.08,
  "load_15": 0.05,
  "uptime": "3d 2h"
}
```

---

## Logs

### Get recent log lines

`GET /{panel_path}/api/logs`

Returns recent log lines from the log file if configured, otherwise from the in-memory buffer (capped at 2000 lines).

**Query parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `lines` | integer | `200` | Number of most recent lines to return |

**Response**

```json
{
  "lines": [
    "2026-05-18 12:00:00 INFO panel panel at http://0.0.0.0:2053/secret/",
    "2026-05-18 12:00:01 INFO inbound started inbound-ws on port 443"
  ]
}
```

---

### Stream logs (SSE)

`GET /{panel_path}/api/logs/stream`

Opens a Server-Sent Events stream that pushes new log lines in real time. A `: ping` keepalive comment is emitted every 15 seconds when no log lines are produced.

**Response** `Content-Type: text/event-stream`

```
data: 2026-05-18 12:01:00 INFO core new connection from 1.2.3.4

data: 2026-05-18 12:01:01 INFO traffic client Alice: 1024 bytes up

: ping
```

---

## Config

### Get current config

`GET /{panel_path}/api/config`

Returns the active in-memory panel configuration.

**Response**

```json
{
  "panel_host": "0.0.0.0",
  "panel_port": 2053,
  "panel_path": "yourSecretPath",
  "panel_threads": 4,
  "log_level": "info",
  "log_file": "/var/log/ghostnode.log",
  "hostname": "",
  "auto_update": true
}
```

---

### Save config

`POST /{panel_path}/api/config`

Writes updated values to the TOML config file on disk. All fields are optional; only provided fields are written. A server restart is required for most changes to take effect.

**Request body**

| Field | Type | Description |
|-------|------|-------------|
| `panel_host` | string | Panel bind address |
| `panel_port` | integer | Panel port |
| `panel_path` | string | URI auth token / panel secret path |
| `panel_threads` | integer | Number of Waitress worker threads |
| `log_level` | string | Logging level (`debug`, `info`, `warning`, `error`) |
| `log_file` | string | Path to log file |
| `hostname` | string | Server hostname used in config link generation |
| `auto_update` | boolean | Enable automatic updates |

**Response**

```json
{"ok": true}
```

---

## System

### Get version

`GET /{panel_path}/api/version`

**Response**

```json
{"version": "0.18.31"}
```

---

### Restart server

`POST /{panel_path}/api/restart`

Schedules a process restart via `os.execv`. The response is returned before the restart occurs (~0.5 second delay).

**Response**

```json
{"ok": true}
```
