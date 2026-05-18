#!/bin/bash
set -e

echo "Building GhostNode binaries..."

cd "$(dirname "$0")/.."

echo "Building ghostnode (server)..."
python3.13 -m PyInstaller --onefile --name ghostnode \
    --add-data "panel/frontend:panel/frontend" \
    --hidden-import transport.websocket \
    --hidden-import transport.http_request \
    --hidden-import transport.http_request_sse \
    --hidden-import transport.http_request_body \
    --hidden-import transport.http2 \
    --hidden-import transport.grpc \
    main.py

echo "Building ghostnode-client..."
python3.13 -m PyInstaller --onefile --name ghostnode-client \
    --hidden-import transport.websocket \
    --hidden-import transport.http_request \
    --hidden-import transport.http_request_sse \
    --hidden-import transport.http_request_body \
    --hidden-import transport.http2 \
    --hidden-import transport.grpc \
    --hidden-import core.protocol \
    --hidden-import core.crypto \
    --hidden-import client.connector \
    --hidden-import client.socks5 \
    client/main.py

echo "Generating checksums..."
cd dist
sha256sum ghostnode > ghostnode.sha256
sha256sum ghostnode-client > ghostnode-client.sha256
cd ..

echo "Build complete!"
ls -lh dist/
