#!/bin/bash
set -e

GITHUB_REPO="FrenchToblerone54/GhostNode"
VERSION="latest"

echo "GhostNode Installation"
echo "======================"

if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (use sudo)"
    exit 1
fi

ARCH=$(uname -m)
if [ "$ARCH" = "x86_64" ]; then
    BIN_NAME="ghostnode"
elif [ "$ARCH" = "aarch64" ]; then
    BIN_NAME="ghostnode-arm64"
else
    echo "Error: Unsupported architecture: $ARCH. Only x86_64 and aarch64 are supported."
    exit 1
fi

OS=$(uname -s)
if [ "$OS" != "Linux" ]; then
    echo "Error: Only Linux is supported"
    exit 1
fi

echo "Downloading GhostNode..."
wget -q --show-progress "https://github.com/${GITHUB_REPO}/releases/${VERSION}/download/${BIN_NAME}" -O /tmp/ghostnode
wget -q "https://github.com/${GITHUB_REPO}/releases/${VERSION}/download/${BIN_NAME}.sha256" -O /tmp/ghostnode.sha256

echo "Verifying checksum..."
cd /tmp
sha256sum -c ghostnode.sha256

echo "Installing binary..."
install -m 755 /tmp/ghostnode /usr/local/bin/ghostnode

CONFIG_DIR="/etc/ghostnode"
GEO_DIR="$CONFIG_DIR/geo"
mkdir -p "$CONFIG_DIR" "$GEO_DIR"

if [ ! -f "$CONFIG_DIR/config.toml" ]; then
    echo ""
    echo "Configuration"
    echo "-------------"

    PANEL_PATH=$(/usr/local/bin/ghostnode --generate-token)

    read -p "Panel listen host [127.0.0.1]: " HOST
    HOST=${HOST:-127.0.0.1}
    read -p "Panel listen port [9090]: " PORT
    PORT=${PORT:-9090}

    read -p "Server hostname for link generation (leave empty to auto-detect): " HOSTNAME

    read -p "Panel worker threads [4]: " PANEL_THREADS
    PANEL_THREADS=${PANEL_THREADS:-4}

    echo ""
    read -p "Update proxy URL (leave empty if not needed): " UPDATE_PROXY

    echo ""
    read -p "Enable auto-update? [Y/n]: " AUTO_UPDATE_INPUT
    AUTO_UPDATE_INPUT=${AUTO_UPDATE_INPUT:-y}
    if [[ $AUTO_UPDATE_INPUT =~ ^[Yy]$ ]]; then
        AUTO_UPDATE="true"
    else
        AUTO_UPDATE="false"
    fi

    touch /var/log/ghostnode.log

    cat > "$CONFIG_DIR/config.toml" <<EOF
[panel]
enabled = true
host = "${HOST}"
port = ${PORT}
path = "${PANEL_PATH}"
threads = ${PANEL_THREADS}

[database]
path = "${CONFIG_DIR}/ghostnode.db"

[geo]
geoip_path = "${GEO_DIR}/GeoLite2-Country.mmdb"
geoip_dat_path = "${GEO_DIR}/geoip.dat"
geosite_path = "${GEO_DIR}/geosite.dat"

[logging]
level = "info"
file = "/var/log/ghostnode.log"

[server]
hostname = "${HOSTNAME}"
auto_update = ${AUTO_UPDATE}
update_check_interval = 300
update_check_on_startup = true
update_proxy = "${UPDATE_PROXY}"
EOF

    chmod 600 "$CONFIG_DIR/config.toml"
    echo "Config written: $CONFIG_DIR/config.toml"
else
    echo "Config already exists at $CONFIG_DIR/config.toml"
    HOST=$(grep "^host" "$CONFIG_DIR/config.toml" | head -1 | awk -F'"' '{print $2}')
    PORT=$(grep "^port" "$CONFIG_DIR/config.toml" | head -1 | awk '{print $3}')
    PANEL_PATH=$(grep "^path" "$CONFIG_DIR/config.toml" | head -1 | awk -F'"' '{print $2}')
    HOST=${HOST:-127.0.0.1}
    PORT=${PORT:-9090}
fi

echo ""
echo "Downloading geo data..."
wget -q --show-progress "https://github.com/v2fly/geoip/releases/latest/download/geoip.dat" -O "$GEO_DIR/geoip.dat" || echo "geoip.dat download failed, skipping"
wget -q --show-progress "https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat" -O "$GEO_DIR/geosite.dat" || echo "geosite.dat download failed, skipping"

echo "Installing systemd service..."
cat > /etc/systemd/system/ghostnode.service <<EOF
[Unit]
Description=GhostNode Proxy Server
After=network.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/ghostnode -c ${CONFIG_DIR}/config.toml
Restart=always
RestartSec=3
User=root
StandardOutput=append:/var/log/ghostnode.log
StandardError=append:/var/log/ghostnode.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

read -p "Configure nginx with TLS? [y/N]: " -n 1 -r SETUP_NGINX
echo
if [[ $SETUP_NGINX =~ ^[Yy]$ ]]; then
    apt-get update -qq && apt-get install -y -qq nginx certbot python3-certbot-nginx

    if [ -f /etc/nginx/sites-available/ghostnode ]; then
        rm -f /etc/nginx/sites-enabled/ghostnode /etc/nginx/sites-available/ghostnode
        systemctl is-active --quiet nginx && systemctl reload nginx
    fi

    read -p "Domain name: " DOMAIN

    cat > /etc/nginx/sites-available/ghostnode <<EOF
server {
    listen 80;
    server_name ${DOMAIN};
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }
}
EOF

    ln -sf /etc/nginx/sites-available/ghostnode /etc/nginx/sites-enabled/
    nginx -t && systemctl reload nginx

    read -p "Generate TLS certificate with Let's Encrypt? [Y/n]: " -n 1 -r TLS
    echo
    if [[ ! $TLS =~ ^[Nn]$ ]]; then
        certbot --nginx -d "${DOMAIN}"
    fi

    cat > /etc/nginx/sites-available/ghostnode <<EOF
server {
    listen 80;
    server_name ${DOMAIN};
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }
    location / {
        return 301 https://\$server_name\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name ${DOMAIN};

    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://${HOST}:${PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
        proxy_buffering off;
    }
}
EOF

    nginx -t && systemctl reload nginx
    echo "nginx configured for ${DOMAIN}"
    BASE_URL="https://${DOMAIN}"
else
    BASE_URL="http://${HOST}:${PORT}"
fi

echo "Enabling and starting GhostNode..."
systemctl enable ghostnode
if systemctl is-active --quiet ghostnode; then
    systemctl restart ghostnode
else
    systemctl start ghostnode
fi

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║              GhostNode Installation Complete             ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║                                                          ║"
echo "║  Panel URL:                                              ║"
echo "║  ${BASE_URL}/${PANEL_PATH}/                              ║"
echo "║                                                          ║"
echo "║  ⚠  Save this URL! It is your admin panel access path.  ║"
echo "║                                                          ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Useful commands:                                        ║"
echo "║  sudo systemctl status ghostnode                         ║"
echo "║  sudo systemctl restart ghostnode                        ║"
echo "║  sudo journalctl -u ghostnode -f                         ║"
echo "╚══════════════════════════════════════════════════════════╝"
