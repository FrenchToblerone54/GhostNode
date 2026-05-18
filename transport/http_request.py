#!/usr/bin/env python3.13
import asyncio
import base64
import logging
import ssl
import time
from nanoid import generate as _nanoid
from aiohttp import ClientSession,ClientTimeout,TCPConnector,web
from urllib.parse import urlparse

logger=logging.getLogger(__name__)
POLL_TIMEOUT=25
MAX_BATCH=65536

class HTTPRequestStreamReader:
    def __init__(self):
        self._buf=bytearray()
        self._event=asyncio.Event()
        self._closed=False

    def feed(self, data):
        self._buf.extend(data)
        self._event.set()

    def close(self):
        self._closed=True
        self._event.set()

    async def read(self, n=-1):
        while not self._buf and not self._closed:
            self._event.clear()
            await self._event.wait()
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
            self._event.clear()
            await self._event.wait()
        if len(self._buf)<n:
            raise asyncio.IncompleteReadError(bytes(self._buf),n)
        data=bytes(self._buf[:n])
        del self._buf[:n]
        return data

class HTTPRequestStreamWriter:
    def __init__(self, session_id, base_url, session, ssl_ctx, stop_event):
        self._sid=session_id
        self._url=base_url
        self._session=session
        self._ssl=ssl_ctx
        self._stop=stop_event
        self._buf=bytearray()
        self._closed=False
        self._task=None

    def write(self, data):
        if not self._closed:
            self._buf.extend(data)

    async def drain(self):
        if not self._buf or self._closed:
            return
        data=bytes(self._buf)
        self._buf.clear()
        try:
            async with self._session.post(
                f"{self._url}/gn-up",
                data=base64.b64encode(data),
                headers={"X-Session":self._sid},
                ssl=self._ssl
            ) as resp:
                pass
        except Exception as e:
            logger.debug(f"http-request upload error: {e}")

    def close(self):
        self._closed=True
        self._stop.set()
        for t in getattr(self,"_tasks",[self._task] if self._task else []):
            t.cancel()
        asyncio.ensure_future(self._session.close())

class HTTPRequestServerSession:
    def __init__(self):
        self._up_queue=asyncio.Queue()
        self._down_buf=bytearray()
        self._created=time.time()
        self._last_seen=time.time()

    def touch(self):
        self._last_seen=time.time()

    def is_expired(self, ttl=60):
        return time.time()-self._last_seen>ttl

_sessions={}

async def serve(host, port, path, handler, ssl_cert="", ssl_key=""):
    ssl_ctx=None
    if ssl_cert and ssl_key:
        ssl_ctx=ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(ssl_cert,ssl_key)
    app=web.Application()

    async def handle_init(request):
        sid=_nanoid(size=20)
        sess=HTTPRequestServerSession()
        sess.reader=None
        _sessions[sid]=sess
        stream_reader=HTTPRequestStreamReader()
        sess.reader=stream_reader
        stop_event=asyncio.Event()

        class SvrWriter:
            def __init__(self):
                self._buf=bytearray()
                self._closed=False
            def write(self, data):
                if not self._closed:
                    self._buf.extend(data)
            async def drain(self):
                if self._buf:
                    sess._down_buf.extend(self._buf)
                    self._buf.clear()
            def close(self):
                self._closed=True
                stop_event.set()

        svr_writer=SvrWriter()
        asyncio.create_task(handler(stream_reader,svr_writer))
        return web.Response(text=sid)

    async def handle_upload(request):
        sid=request.headers.get("X-Session","")
        sess=_sessions.get(sid)
        if not sess:
            return web.Response(status=404)
        sess.touch()
        body=await request.read()
        if body and sess.reader:
            sess.reader.feed(base64.b64decode(body))
        return web.Response(text="ok")

    async def handle_download(request):
        sid=request.headers.get("X-Session","")
        sess=_sessions.get(sid)
        if not sess:
            return web.Response(status=404)
        sess.touch()
        deadline=time.time()+POLL_TIMEOUT
        while not sess._down_buf and time.time()<deadline:
            await asyncio.sleep(0.05)
        data=bytes(sess._down_buf[:MAX_BATCH])
        del sess._down_buf[:MAX_BATCH]
        return web.Response(body=base64.b64encode(data) if data else b"",content_type="application/octet-stream")

    app.router.add_post(path+"/gn-init",handle_init)
    app.router.add_post(path+"/gn-up",handle_upload)
    app.router.add_get(path+"/gn-down",handle_download)
    runner=web.AppRunner(app)
    await runner.setup()
    site=web.TCPSite(runner,host,port,ssl_context=ssl_ctx)
    await site.start()
    logger.info(f"http-request inbound on {host}:{port}{path}")

    class _Server:
        def close(self):
            asyncio.ensure_future(runner.cleanup())
        async def wait_closed(self):
            await asyncio.sleep(0.1)

    return _Server()

def make_server_handler(path, handler, host_header=""):
    async def _handle_init(request):
        sid=_nanoid(size=20)
        sess=HTTPRequestServerSession()
        sess.reader=None
        _sessions[sid]=sess
        stream_reader=HTTPRequestStreamReader()
        sess.reader=stream_reader
        stop_event=asyncio.Event()
        class SvrWriter:
            def __init__(self):
                self._buf=bytearray()
                self._closed=False
            def write(self, data):
                if not self._closed:
                    self._buf.extend(data)
            async def drain(self):
                if self._buf:
                    sess._down_buf.extend(self._buf)
                    self._buf.clear()
            def close(self):
                self._closed=True
                stop_event.set()
        asyncio.create_task(handler(stream_reader, SvrWriter()))
        return web.Response(text=sid)
    async def _handle_upload(request):
        sid=request.headers.get("X-Session", "")
        sess=_sessions.get(sid)
        if not sess:
            return web.Response(status=404)
        sess.touch()
        body=await request.read()
        if body and sess.reader:
            sess.reader.feed(base64.b64decode(body))
        return web.Response(text="ok")
    async def _handle_download(request):
        sid=request.headers.get("X-Session", "")
        sess=_sessions.get(sid)
        if not sess:
            return web.Response(status=404)
        sess.touch()
        deadline=time.time()+POLL_TIMEOUT
        while not sess._down_buf and time.time()<deadline:
            await asyncio.sleep(0.05)
        data=bytes(sess._down_buf[:MAX_BATCH])
        del sess._down_buf[:MAX_BATCH]
        return web.Response(body=base64.b64encode(data) if data else b"", content_type="application/octet-stream")
    async def dispatch(request):
        rp=request.path
        if rp==path+"/gn-init":
            return await _handle_init(request)
        if rp==path+"/gn-up":
            return await _handle_upload(request)
        if rp==path+"/gn-down":
            return await _handle_download(request)
        return web.Response(status=404)
    return dispatch

async def connect(url, path, sni="", allow_insecure=False, extra=None, host_header=""):
    ssl_ctx=None
    if url.startswith("https://") or url.startswith("wss://"):
        ssl_ctx=ssl.create_default_context()
        if allow_insecure:
            ssl_ctx.check_hostname=False
            ssl_ctx.verify_mode=ssl.CERT_NONE
    base_url=url.rstrip("/")+path
    connector=TCPConnector()
    _base_headers={"Host":host_header} if host_header else {}
    session=ClientSession(connector=connector,timeout=ClientTimeout(total=60),headers=_base_headers)
    _init_headers={"X-Poll-Connections":str((extra or {}).get("poll_connections",1))}
    async with session.post(f"{base_url}/gn-init",ssl=ssl_ctx,headers=_init_headers) as resp:
        sid=await resp.text()
    stop_event=asyncio.Event()
    stream_reader=HTTPRequestStreamReader()
    stream_writer=HTTPRequestStreamWriter(sid,base_url,session,ssl_ctx,stop_event)

    async def poll_loop():
        while not stop_event.is_set():
            try:
                async with session.get(f"{base_url}/gn-down",headers={"X-Session":sid},ssl=ssl_ctx) as resp:
                    body=await resp.read()
                    if body:
                        stream_reader.feed(base64.b64decode(body))
            except Exception as e:
                logger.debug(f"http-request poll error: {e}")
                await asyncio.sleep(1)

    _pc=(extra or {}).get("poll_connections",1)
    _tasks=[asyncio.create_task(poll_loop()) for _ in range(_pc)]
    stream_writer._task=_tasks[0]
    stream_writer._tasks=_tasks
    return stream_reader,stream_writer
