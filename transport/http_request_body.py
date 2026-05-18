#!/usr/bin/env python3.13
import asyncio
import logging
import ssl
import time
from nanoid import generate as _nanoid
from aiohttp import ClientSession,ClientTimeout,TCPConnector,web

logger=logging.getLogger(__name__)

class HRBStreamReader:
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

class HRBStreamWriter:
    def __init__(self, session_id, base_url, session, ssl_ctx, stop_event):
        self._sid=session_id
        self._url=base_url
        self._session=session
        self._ssl=ssl_ctx
        self._stop=stop_event
        self._buf=bytearray()
        self._queue=asyncio.Queue()
        self._closed=False

    def write(self, data):
        if not self._closed:
            self._buf.extend(data)

    async def drain(self):
        if not self._buf or self._closed:
            return
        data=bytes(self._buf)
        self._buf.clear()
        await self._queue.put(data)

    def close(self):
        self._closed=True
        self._stop.set()
        self._queue.put_nowait(None)

_sessions={}

async def serve(host, port, path, handler, ssl_cert="", ssl_key="", host_header=""):
    ssl_ctx=None
    if ssl_cert and ssl_key:
        ssl_ctx=ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(ssl_cert,ssl_key)
    app=web.Application()

    async def handle_session(request):
        if host_header and request.headers.get("Host","").split(":")[0]!=host_header.split(":")[0]:
            return web.Response(status=404)
        sid=_nanoid(size=20)
        up_reader=HRBStreamReader()
        down_queue=asyncio.Queue()
        stop_event=asyncio.Event()
        _sessions[sid]={"up":up_reader,"down":down_queue,"stop":stop_event,"last":time.time()}

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

        async def read_upload():
            try:
                async for chunk in request.content:
                    if chunk:
                        up_reader.feed(chunk)
            except Exception:
                pass
            up_reader.close()

        asyncio.create_task(read_upload())
        resp=web.StreamResponse(headers={"Content-Type":"application/octet-stream","Cache-Control":"no-cache","X-Session":sid,"X-Accel-Buffering":"no"})
        await resp.prepare(request)
        try:
            while not stop_event.is_set():
                try:
                    chunk=await asyncio.wait_for(down_queue.get(),timeout=15)
                    if chunk is None:
                        break
                    await resp.write(chunk)
                except asyncio.TimeoutError:
                    await resp.write(b"\x00")
        except Exception:
            pass
        _sessions.pop(sid,None)
        return resp

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

    app.router.add_post(path+"/gn-conn",handle_session)
    app.router.add_post(path+"/gn-up",handle_upload)
    runner=web.AppRunner(app)
    await runner.setup()
    site=web.TCPSite(runner,host,port,ssl_context=ssl_ctx)
    await site.start()
    logger.info(f"http-request-body inbound on {host}:{port}{path}")

    class _Server:
        def close(self):
            asyncio.ensure_future(runner.cleanup())
        async def wait_closed(self):
            await asyncio.sleep(0.1)

    return _Server()

def make_server_handler(path, handler, host_header=""):
    async def _handle_session(request):
        if host_header and request.headers.get("Host", "").split(":")[0]!=host_header.split(":")[0]:
            return web.Response(status=404)
        sid=_nanoid(size=20)
        up_reader=HRBStreamReader()
        down_queue=asyncio.Queue()
        stop_event=asyncio.Event()
        _sessions[sid]={"up":up_reader, "down":down_queue, "stop":stop_event, "last":time.time()}
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
        async def read_upload():
            try:
                async for chunk in request.content:
                    if chunk:
                        up_reader.feed(chunk)
            except Exception:
                pass
            up_reader.close()
        asyncio.create_task(read_upload())
        resp=web.StreamResponse(headers={"Content-Type":"application/octet-stream", "Cache-Control":"no-cache", "X-Session":sid, "X-Accel-Buffering":"no"})
        await resp.prepare(request)
        try:
            while not stop_event.is_set():
                try:
                    chunk=await asyncio.wait_for(down_queue.get(), timeout=15)
                    if chunk is None:
                        break
                    await resp.write(chunk)
                except asyncio.TimeoutError:
                    await resp.write(b"\x00")
        except Exception:
            pass
        _sessions.pop(sid, None)
        return resp
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
    async def dispatch(request):
        rp=request.path
        if rp==path+"/gn-conn":
            return await _handle_session(request)
        if rp==path+"/gn-up":
            return await _handle_upload(request)
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
    _base_headers={"Host":host_header} if host_header else {}
    connector=TCPConnector()
    stop_event=asyncio.Event()
    stream_reader=HRBStreamReader()
    upload_queue=asyncio.Queue()

    class UploadGen:
        def __aiter__(self):
            return self
        async def __anext__(self):
            chunk=await upload_queue.get()
            if chunk is None:
                raise StopAsyncIteration
            return chunk

    session=ClientSession(connector=connector,timeout=ClientTimeout(total=None,connect=10),headers=_base_headers)

    async def run_conn():
        try:
            async with session.post(
                f"{base_url}/gn-conn",
                data=UploadGen(),
                headers={"Content-Type":"application/octet-stream","Transfer-Encoding":"chunked"},
                ssl=ssl_ctx
            ) as resp:
                async for chunk in resp.content:
                    if stop_event.is_set():
                        break
                    if chunk and chunk!=b"\x00":
                        stream_reader.feed(chunk)
        except Exception as e:
            if not stop_event.is_set():
                logger.debug(f"http-request-body conn error: {e}")
        stream_reader.close()

    _conn_task=asyncio.create_task(run_conn())

    class _Writer:
        def __init__(self):
            self._buf=bytearray()
            self._closed=False
        def write(self,data):
            if not self._closed:
                self._buf.extend(data)
        async def drain(self):
            if not self._buf or self._closed:
                return
            data=bytes(self._buf)
            self._buf.clear()
            await upload_queue.put(data)
        def close(self):
            self._closed=True
            stop_event.set()
            upload_queue.put_nowait(None)
            _conn_task.cancel()
            asyncio.ensure_future(session.close())

    return stream_reader,_Writer()
