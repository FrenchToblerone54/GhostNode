#!/usr/bin/env python3.13
import asyncio
import base64
import logging
import ssl
import time
from nanoid import generate as _nanoid
from aiohttp import ClientSession,ClientTimeout,TCPConnector,web

logger=logging.getLogger(__name__)

class SSEStreamReader:
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

class SSEStreamWriter:
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
                data=data,
                headers={"X-Session":self._sid,"Content-Type":"application/octet-stream"},
                ssl=self._ssl
            ) as resp:
                pass
        except Exception as e:
            logger.debug(f"sse upload error: {e}")

    def close(self):
        self._closed=True
        self._stop.set()
        if self._task:
            self._task.cancel()
        asyncio.ensure_future(self._session.close())

_sessions={}

async def serve(host, port, path, handler, ssl_cert="", ssl_key=""):
    ssl_ctx=None
    if ssl_cert and ssl_key:
        ssl_ctx=ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(ssl_cert,ssl_key)
    app=web.Application()

    async def handle_init(request):
        sid=_nanoid(size=20)
        up_reader=SSEStreamReader()
        down_queue=asyncio.Queue()
        stop_event=asyncio.Event()
        _sessions[sid]={"up":up_reader,"down":down_queue,"created":time.time(),"last":time.time(),"stop":stop_event}
        class SvrWriter:
            def __init__(self):
                self._buf=bytearray()
                self._closed=False
            def write(self,data):
                if not self._closed:
                    self._buf.extend(data)
            async def drain(self):
                if self._buf:
                    down_queue.put_nowait(bytes(self._buf))
                    self._buf.clear()
            def close(self):
                self._closed=True
                stop_event.set()
                down_queue.put_nowait(None)
        asyncio.create_task(handler(up_reader,SvrWriter()))
        return web.Response(text=sid)

    async def handle_upload(request):
        sid=request.headers.get("X-Session","")
        sess=_sessions.get(sid)
        if not sess:
            return web.Response(status=404)
        sess["last"]=time.time()
        data=await request.read()
        if data:
            sess["up"].feed(data)
        return web.Response(text="ok")

    async def handle_sse(request):
        sid=request.query.get("sid","") or request.headers.get("X-Session","")
        sess=_sessions.get(sid)
        if not sess:
            return web.Response(status=404)
        sess["last"]=time.time()
        resp=web.StreamResponse(headers={"Content-Type":"text/event-stream","Cache-Control":"no-cache","X-Accel-Buffering":"no"})
        await resp.prepare(request)
        stop_event=sess["stop"]
        down_queue=sess["down"]
        try:
            while not stop_event.is_set():
                try:
                    chunk=await asyncio.wait_for(down_queue.get(),timeout=15)
                    if chunk is None:
                        break
                    await resp.write(f"data: {base64.b64encode(chunk).decode()}\n\n".encode())
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
        except Exception:
            pass
        return resp

    app.router.add_post(path+"/gn-init",handle_init)
    app.router.add_post(path+"/gn-up",handle_upload)
    app.router.add_get(path+"/gn-sse",handle_sse)
    runner=web.AppRunner(app)
    await runner.setup()
    site=web.TCPSite(runner,host,port,ssl_context=ssl_ctx)
    await site.start()
    logger.info(f"sse inbound on {host}:{port}{path}")

    class _Server:
        def close(self):
            asyncio.ensure_future(runner.cleanup())
        async def wait_closed(self):
            await asyncio.sleep(0.1)

    return _Server()

def make_server_handler(path, handler, host_header=""):
    async def _handle_init(request):
        sid=_nanoid(size=20)
        up_reader=SSEStreamReader()
        down_queue=asyncio.Queue()
        stop_event=asyncio.Event()
        _sessions[sid]={"up":up_reader, "down":down_queue, "created":time.time(), "last":time.time(), "stop":stop_event}
        class SvrWriter:
            def __init__(self):
                self._buf=bytearray()
                self._closed=False
            def write(self, data):
                if not self._closed:
                    self._buf.extend(data)
            async def drain(self):
                if self._buf:
                    down_queue.put_nowait(bytes(self._buf))
                    self._buf.clear()
            def close(self):
                self._closed=True
                stop_event.set()
                down_queue.put_nowait(None)
        asyncio.create_task(handler(up_reader, SvrWriter()))
        return web.Response(text=sid)
    async def _handle_upload(request):
        sid=request.headers.get("X-Session", "")
        sess=_sessions.get(sid)
        if not sess:
            return web.Response(status=404)
        sess["last"]=time.time()
        data=await request.read()
        if data:
            sess["up"].feed(data)
        return web.Response(text="ok")
    async def _handle_sse(request):
        sid=request.query.get("sid", "") or request.headers.get("X-Session", "")
        sess=_sessions.get(sid)
        if not sess:
            return web.Response(status=404)
        sess["last"]=time.time()
        resp=web.StreamResponse(headers={"Content-Type":"text/event-stream", "Cache-Control":"no-cache", "X-Accel-Buffering":"no"})
        await resp.prepare(request)
        stop_event=sess["stop"]
        down_queue=sess["down"]
        try:
            while not stop_event.is_set():
                try:
                    chunk=await asyncio.wait_for(down_queue.get(), timeout=15)
                    if chunk is None:
                        break
                    await resp.write(f"data: {base64.b64encode(chunk).decode()}\n\n".encode())
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
        except Exception:
            pass
        return resp
    async def dispatch(request):
        rp=request.path
        if rp==path+"/gn-init":
            return await _handle_init(request)
        if rp==path+"/gn-up":
            return await _handle_upload(request)
        if rp==path+"/gn-sse":
            return await _handle_sse(request)
        return web.Response(status=404)
    return dispatch

async def connect(url, path, sni="", allow_insecure=False, extra=None, host_header=""):
    ssl_ctx=None
    if url.startswith("https://") or url.startswith("wss://"):
        ssl_ctx=ssl.create_default_context()
        if sni:
            ssl_ctx.server_hostname=sni
        if allow_insecure:
            ssl_ctx.check_hostname=False
            ssl_ctx.verify_mode=ssl.CERT_NONE
    base_url=url.rstrip("/")+path
    connector=TCPConnector()
    _base_headers={"Host":host_header} if host_header else {}
    session=ClientSession(connector=connector,timeout=ClientTimeout(total=None,connect=10),headers=_base_headers)
    async with session.post(f"{base_url}/gn-init",ssl=ssl_ctx) as resp:
        sid=await resp.text()
    stop_event=asyncio.Event()
    stream_reader=SSEStreamReader()
    stream_writer=SSEStreamWriter(sid,base_url,session,ssl_ctx,stop_event)

    async def sse_loop():
        while not stop_event.is_set():
            try:
                async with session.get(
                    f"{base_url}/gn-sse",
                    headers={"X-Session":sid},
                    ssl=ssl_ctx,
                    timeout=ClientTimeout(total=None,connect=10)
                ) as resp:
                    buf=b""
                    async for chunk in resp.content:
                        if stop_event.is_set():
                            break
                        buf+=chunk
                        while b"\n\n" in buf:
                            event,buf=buf.split(b"\n\n",1)
                            for line in event.split(b"\n"):
                                if line.startswith(b"data: "):
                                    stream_reader.feed(base64.b64decode(line[6:]))
            except Exception as e:
                if not stop_event.is_set():
                    logger.debug(f"sse receive error: {e}")
                    await asyncio.sleep(1)

    stream_writer._task=asyncio.create_task(sse_loop())
    return stream_reader,stream_writer
