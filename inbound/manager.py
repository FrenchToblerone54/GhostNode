#!/usr/bin/env python3.13
import asyncio
import logging

logger=logging.getLogger(__name__)

_HTTP_TRANSPORTS={"websocket","http-request","http-request-sse","http-request-body"}

class _VirtualMuxServer:
    def __init__(self, mux, path, mux_key, port_muxes):
        self._mux=mux
        self._path=path
        self._mux_key=mux_key
        self._port_muxes=port_muxes

    def close(self):
        self._mux.remove(self._path)
        if self._mux.empty():
            asyncio.ensure_future(self._stop_mux())

    async def _stop_mux(self):
        await self._mux.stop()
        self._port_muxes.pop(self._mux_key, None)

    async def wait_closed(self):
        await asyncio.sleep(0.1)

class InboundManager:
    def __init__(self, db, router, traffic_ctrl):
        self._db=db
        self._router=router
        self._traffic_ctrl=traffic_ctrl
        self._servers={}
        self._port_muxes={}

    async def start_all(self):
        inbounds=await self._db.get_inbounds()
        for inb in inbounds:
            if inb["enabled"]:
                await self._start_inbound(inb)

    async def stop_all(self):
        for tag in list(self._servers.keys()):
            await self._stop_inbound(tag)

    async def restart_inbound(self, tag):
        await self._stop_inbound(tag)
        inbounds=await self._db.get_inbounds()
        for inb in inbounds:
            if inb["tag"]==tag and inb["enabled"]:
                await self._start_inbound(inb)
                break

    async def _start_inbound(self, inb):
        tag=inb["tag"]
        transport=inb.get("transport", "websocket")
        listen_ip=inb.get("listen_ip", "0.0.0.0")
        port=inb["port"]
        path=inb.get("path", "/gn")
        ssl_cert=inb.get("ssl_cert", "")
        ssl_key=inb.get("ssl_key", "")
        host_header=inb.get("host", "")
        handler=self._make_handler(tag, inb)
        try:
            if transport in _HTTP_TRANSPORTS:
                mux_key=(listen_ip, port, ssl_cert, ssl_key)
                if mux_key not in self._port_muxes:
                    from inbound.port_mux import PortMux
                    mux=PortMux(listen_ip, port, ssl_cert, ssl_key)
                    await mux.start()
                    self._port_muxes[mux_key]=mux
                mux=self._port_muxes[mux_key]
                ws_send_batch_bytes=inb.get("ws_send_batch_bytes",65536)
                max_upload_bytes=inb.get("max_upload_bytes",1048576)
                max_download_bytes=inb.get("max_download_bytes",1048576)
                min_download_ms=inb.get("min_download_ms",0)
                if transport=="websocket":
                    from transport.websocket import make_server_handler
                    aio_handler=make_server_handler(path, handler, host_header, ws_send_batch_bytes=ws_send_batch_bytes)
                elif transport=="http-request":
                    from transport.http_request import make_server_handler
                    aio_handler=make_server_handler(path, handler, host_header, max_download_bytes=max_download_bytes, min_download_ms=min_download_ms)
                elif transport=="http-request-sse":
                    from transport.http_request_sse import make_server_handler
                    aio_handler=make_server_handler(path, handler, host_header)
                elif transport=="http-request-body":
                    from transport.http_request_body import make_server_handler
                    aio_handler=make_server_handler(path, handler, host_header)
                mux.add(path, aio_handler)
                self._servers[tag]=_VirtualMuxServer(mux, path, mux_key, self._port_muxes)
            elif transport=="http2":
                from transport.http2 import serve as h2_serve
                server=await h2_serve(listen_ip, port, path, handler, ssl_cert, ssl_key)
                self._servers[tag]=server
            elif transport=="grpc":
                from transport.grpc import serve as grpc_serve
                server=await grpc_serve(listen_ip, port, handler, ssl_cert, ssl_key)
                self._servers[tag]=server
            else:
                server=await asyncio.start_server(handler, listen_ip, port)
                self._servers[tag]=server
            logger.info(f"inbound [{tag}] started on {listen_ip}:{port} ({transport})")
        except Exception as e:
            logger.error(f"failed to start inbound [{tag}]: {e}")

    async def _stop_inbound(self, tag):
        server=self._servers.pop(tag, None)
        if server:
            server.close()
            try:
                await server.wait_closed()
            except Exception:
                pass
            logger.info(f"inbound [{tag}] stopped")

    def _make_handler(self, tag, inb_cfg=None):
        from inbound.handler import handle_connection
        db=self._db
        router=self._router
        traffic_ctrl=self._traffic_ctrl
        async def handler(reader, writer):
            await handle_connection(reader, writer, tag, db, router, traffic_ctrl, inb_cfg)
        return handler
