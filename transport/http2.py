#!/usr/bin/env python3.13
import asyncio
import ssl
import struct
import logging
from urllib.parse import urlparse
from h2.connection import H2Connection
from h2.events import RequestReceived,DataReceived,StreamEnded,WindowUpdated,RemoteSettingsChanged,StreamReset,ConnectionTerminated
from h2.config import H2Configuration

logger=logging.getLogger(__name__)

class H2StreamReader:
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

class H2StreamWriter:
    def __init__(self, stream_id, conn, raw_writer, conn_lock, window_event, stop_event):
        self._sid=stream_id
        self._conn=conn
        self._raw=raw_writer
        self._lock=conn_lock
        self._win=window_event
        self._stop=stop_event
        self._buf=bytearray()
        self._closed=False

    def write(self, data):
        if not self._closed:
            self._buf.extend(data)

    async def drain(self):
        if not self._buf or self._closed:
            return
        payload=bytes(self._buf)
        self._buf.clear()
        offset=0
        while offset<len(payload) and not self._stop.is_set():
            async with self._lock:
                win=min(self._conn.local_flow_control_window(self._sid),self._conn.outbound_flow_control_window,self._conn.max_outbound_frame_size)
                if win>0:
                    chunk=payload[offset:offset+win]
                    self._conn.send_data(self._sid,chunk,end_stream=False)
                    out=self._conn.data_to_send()
                else:
                    out=b""
            if win>0:
                self._raw.write(out)
                await self._raw.drain()
                offset+=win
            else:
                self._win.clear()
                try:
                    await asyncio.wait_for(self._win.wait(),timeout=5)
                except asyncio.TimeoutError:
                    pass

    def close(self):
        self._closed=True
        self._stop.set()

async def serve(host, port, path, handler, ssl_cert="", ssl_key=""):
    ssl_ctx=None
    if ssl_cert and ssl_key:
        ssl_ctx=ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(ssl_cert,ssl_key)

    async def handle_raw(raw_reader, raw_writer):
        conn_lock=asyncio.Lock()
        window_event=asyncio.Event()
        window_event.set()
        stop_event=asyncio.Event()
        config=H2Configuration(client_side=False)
        conn=H2Connection(config=config)
        conn.initiate_connection()
        raw_writer.write(conn.data_to_send())
        await raw_writer.drain()
        stream_reader=None
        stream_writer=None
        stream_id=None
        handler_task=None
        try:
            while not stop_event.is_set():
                data=await raw_reader.read(65536)
                if not data:
                    break
                async with conn_lock:
                    events=conn.receive_data(data)
                for event in events:
                    if isinstance(event,RequestReceived):
                        stream_id=event.stream_id
                        async with conn_lock:
                            conn.send_headers(stream_id,[(':status','200'),('content-type','application/octet-stream')],end_stream=False)
                        raw_writer.write(conn.data_to_send())
                        await raw_writer.drain()
                        stream_reader=H2StreamReader()
                        stream_writer=H2StreamWriter(stream_id,conn,raw_writer,conn_lock,window_event,stop_event)
                        handler_task=asyncio.create_task(handler(stream_reader,stream_writer))
                    elif isinstance(event,DataReceived):
                        async with conn_lock:
                            conn.acknowledge_received_data(event.flow_controlled_length,event.stream_id)
                        if stream_reader:
                            stream_reader.feed(event.data)
                    elif isinstance(event,WindowUpdated):
                        window_event.set()
                    elif isinstance(event,RemoteSettingsChanged):
                        window_event.set()
                    elif isinstance(event,(StreamEnded,StreamReset,ConnectionTerminated)):
                        stop_event.set()
                raw_writer.write(conn.data_to_send())
                await raw_writer.drain()
        except Exception as e:
            logger.debug(f"h2 serve error: {e}")
        finally:
            stop_event.set()
            if stream_reader:
                stream_reader.close()
            if handler_task and not handler_task.done():
                handler_task.cancel()
            try:
                raw_writer.close()
            except Exception:
                pass

    server=await asyncio.start_server(handle_raw,host,port,ssl=ssl_ctx)
    logger.info(f"http2 inbound on {host}:{port}{path}")
    return server

async def connect(url, path, sni="", allow_insecure=False, host_header=""):
    parsed=urlparse(url.replace("wss://","https://").replace("ws://","http://"))
    host=parsed.hostname
    port=parsed.port or (443 if parsed.scheme=="https" else 80)
    use_ssl=parsed.scheme=="https"
    ssl_ctx=None
    if use_ssl:
        ssl_ctx=ssl.create_default_context()
        if allow_insecure:
            ssl_ctx.check_hostname=False
            ssl_ctx.verify_mode=ssl.CERT_NONE
        if sni:
            ssl_ctx.check_hostname=not allow_insecure
            server_hostname=sni
        else:
            server_hostname=host
    raw_reader,raw_writer=await asyncio.open_connection(host,port,ssl=ssl_ctx,server_hostname=server_hostname if use_ssl else None)
    conn_lock=asyncio.Lock()
    window_event=asyncio.Event()
    window_event.set()
    stop_event=asyncio.Event()
    config=H2Configuration(client_side=True)
    conn=H2Connection(config=config)
    conn.initiate_connection()
    raw_writer.write(conn.data_to_send())
    await raw_writer.drain()
    stream_id=conn.get_next_available_stream_id()
    authority=host_header if host_header else f"{host}:{port}"
    headers=[(':method','POST'),(':scheme',parsed.scheme),(':authority',authority),(':path',path or '/'),('content-type','application/octet-stream')]
    conn.send_headers(stream_id,headers,end_stream=False)
    raw_writer.write(conn.data_to_send())
    await raw_writer.drain()
    stream_reader=H2StreamReader()
    stream_writer=H2StreamWriter(stream_id,conn,raw_writer,conn_lock,window_event,stop_event)

    async def recv_loop():
        try:
            while not stop_event.is_set():
                data=await raw_reader.read(65536)
                if not data:
                    break
                async with conn_lock:
                    events=conn.receive_data(data)
                for event in events:
                    if isinstance(event,DataReceived):
                        stream_reader.feed(event.data)
                        async with conn_lock:
                            conn.acknowledge_received_data(event.flow_controlled_length,event.stream_id)
                    elif isinstance(event,WindowUpdated):
                        window_event.set()
                    elif isinstance(event,RemoteSettingsChanged):
                        window_event.set()
                    elif isinstance(event,(StreamEnded,StreamReset,ConnectionTerminated)):
                        stop_event.set()
                raw_writer.write(conn.data_to_send())
                await raw_writer.drain()
        except Exception:
            pass
        finally:
            stream_reader.close()
            stop_event.set()

    asyncio.create_task(recv_loop())
    return stream_reader,stream_writer
