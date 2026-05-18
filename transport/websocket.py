#!/usr/bin/env python3.13
import asyncio
import ssl
import logging
from aiohttp import web,WSMsgType,ClientSession,ClientTimeout

logger=logging.getLogger(__name__)
_CHROME_UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"

class WSStreamReader:
    def __init__(self, ws):
        self._ws=ws
        self._buf=bytearray()
        self._closed=False

    async def _fill(self):
        if self._closed:
            return
        try:
            msg=await self._ws.receive()
            if msg.type==WSMsgType.BINARY:
                self._buf.extend(msg.data)
            elif msg.type==WSMsgType.TEXT:
                self._buf.extend(msg.data.encode())
            elif msg.type in (WSMsgType.CLOSE,WSMsgType.CLOSED,WSMsgType.CLOSING,WSMsgType.ERROR):
                self._closed=True
        except Exception:
            self._closed=True

    async def read(self, n=-1):
        if not self._buf and not self._closed:
            await self._fill()
        if not self._buf:
            return b""
        if n<0 or n>=len(self._buf):
            data=bytes(self._buf)
            self._buf.clear()
            return data
        data=bytes(self._buf[:n])
        del self._buf[:n]
        return data

    async def readexactly(self, n):
        while len(self._buf)<n and not self._closed:
            await self._fill()
        if len(self._buf)<n:
            raise asyncio.IncompleteReadError(bytes(self._buf),n)
        data=bytes(self._buf[:n])
        del self._buf[:n]
        return data

class WSStreamWriter:
    def __init__(self, ws, session=None, ws_send_batch_bytes=65536):
        self._ws=ws
        self._session=session
        self._buf=bytearray()
        self._closed=False
        self._batch=ws_send_batch_bytes

    def write(self, data):
        if not self._closed:
            self._buf.extend(data)

    async def drain(self):
        if not self._buf or self._closed:
            return
        data=bytes(self._buf)
        self._buf.clear()
        try:
            for i in range(0, len(data), self._batch):
                await self._ws.send_bytes(data[i:i+self._batch])
        except Exception:
            self._closed=True

    def close(self):
        self._closed=True
        asyncio.ensure_future(self._do_close())

    async def _do_close(self):
        try:
            await self._ws.close()
        except Exception:
            pass
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass

def _make_ssl_context(cert, key):
    ctx=ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(cert,key)
    return ctx

async def serve(host, port, path, handler, ssl_cert="", ssl_key="", host_header="", ws_send_batch_bytes=65536):
    ssl_ctx=None
    if ssl_cert and ssl_key:
        ssl_ctx=_make_ssl_context(ssl_cert,ssl_key)
    app=web.Application()
    async def ws_handler(request):
        if host_header and request.headers.get("Host","").split(":")[0]!=host_header.split(":")[0]:
            return web.Response(status=404)
        ws=web.WebSocketResponse(max_msg_size=0,compress=False,heartbeat=None)
        await ws.prepare(request)
        reader=WSStreamReader(ws)
        writer=WSStreamWriter(ws, ws_send_batch_bytes=ws_send_batch_bytes)
        try:
            await handler(reader,writer)
        except Exception as e:
            logger.debug(f"ws handler error: {e}")
        finally:
            if not ws.closed:
                await ws.close()
        return ws
    app.router.add_get(path,ws_handler)
    runner=web.AppRunner(app)
    await runner.setup()
    site=web.TCPSite(runner,host,port,ssl_context=ssl_ctx)
    await site.start()
    logger.info(f"websocket inbound on {host}:{port}{path}")
    class _Server:
        def close(self):
            asyncio.ensure_future(runner.cleanup())
        async def wait_closed(self):
            await asyncio.sleep(0.1)
    return _Server()

def make_server_handler(path, handler, host_header="", ws_send_batch_bytes=65536):
    async def ws_handler(request):
        if request.path!=path:
            return web.Response(status=404)
        if host_header and request.headers.get("Host","").split(":")[0]!=host_header.split(":")[0]:
            return web.Response(status=404)
        ws=web.WebSocketResponse(max_msg_size=0, compress=False, heartbeat=None)
        await ws.prepare(request)
        reader=WSStreamReader(ws)
        writer=WSStreamWriter(ws, ws_send_batch_bytes=ws_send_batch_bytes)
        try:
            await handler(reader, writer)
        except Exception as e:
            logger.debug(f"ws handler error: {e}")
        finally:
            if not ws.closed:
                await ws.close()
        return ws
    return ws_handler

async def connect(url, path, sni="", allow_insecure=False, host_header="", extra=None):
    ssl_ctx=None
    if url.startswith("wss://"):
        ssl_ctx=ssl.create_default_context()
        if allow_insecure:
            ssl_ctx.check_hostname=False
            ssl_ctx.verify_mode=ssl.CERT_NONE
        if sni:
            ssl_ctx.check_hostname=not allow_insecure
    full_url=url.rstrip("/")+path
    ua=(extra or {}).get("user_agent","") or _CHROME_UA
    ping_iv=(extra or {}).get("ping_interval",20)
    base_url=url.rstrip("/")
    headers={"Host":host_header,"User-Agent":ua,"Origin":base_url} if host_header else {"User-Agent":ua,"Origin":base_url}
    session=ClientSession(timeout=ClientTimeout(total=None,connect=30))
    ws=await session.ws_connect(full_url,ssl=ssl_ctx,max_msg_size=0,compress=False,heartbeat=ping_iv,headers=headers)
    reader=WSStreamReader(ws)
    batch=(extra or {}).get("ws_send_batch_bytes",65536)
    writer=WSStreamWriter(ws,session,ws_send_batch_bytes=batch)
    return reader,writer
