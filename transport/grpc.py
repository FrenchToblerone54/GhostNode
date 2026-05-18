#!/usr/bin/env python3.13
import asyncio
import ssl
import struct
import logging
from urllib.parse import urlparse

logger=logging.getLogger(__name__)

class GrpcStreamReader:
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

def _encode_grpc_frame(data):
    return b"\x00"+struct.pack(">I",len(data))+data

def _decode_grpc_frame(header):
    length=struct.unpack(">I",header[1:5])[0]
    return length

class GrpcStreamWriter:
    def __init__(self, raw_writer):
        self._raw=raw_writer
        self._buf=bytearray()
        self._closed=False

    def write(self, data):
        if not self._closed:
            self._buf.extend(data)

    async def drain(self):
        if not self._buf or self._closed:
            return
        data=bytes(self._buf)
        self._buf.clear()
        frame=_encode_grpc_frame(data)
        self._raw.write(frame)
        try:
            await self._raw.drain()
        except Exception:
            self._closed=True

    def close(self):
        self._closed=True
        try:
            self._raw.close()
        except Exception:
            pass

SERVICE_PATH="/ghostnode.Tunnel/Stream"
HTTP2_PREFACE=b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"

async def serve(host, port, handler, ssl_cert="", ssl_key=""):
    from transport.http2 import serve as h2_serve
    return await h2_serve(host,port,SERVICE_PATH,_wrap_grpc_handler(handler),ssl_cert,ssl_key)

def _wrap_grpc_handler(handler):
    async def wrapped(reader, writer):
        grpc_reader=GrpcStreamReader()
        grpc_writer=GrpcStreamWriter(writer)
        async def forward():
            try:
                while True:
                    hdr=await reader.readexactly(5)
                    length=_decode_grpc_frame(hdr)
                    if length==0:
                        continue
                    data=await reader.readexactly(length)
                    grpc_reader.feed(data)
            except Exception:
                grpc_reader.close()
        fwd_task=asyncio.create_task(forward())
        try:
            await handler(grpc_reader,grpc_writer)
        finally:
            fwd_task.cancel()
    return wrapped

async def connect(url, sni="", allow_insecure=False, host_header=""):
    from transport.http2 import connect as h2_connect
    path=SERVICE_PATH
    r2,w2=await h2_connect(url,path,sni,allow_insecure,host_header)
    grpc_reader=GrpcStreamReader()
    grpc_writer=GrpcStreamWriter(w2)
    async def forward():
        try:
            while True:
                hdr=await r2.readexactly(5)
                length=_decode_grpc_frame(hdr)
                if length==0:
                    continue
                data=await r2.readexactly(length)
                grpc_reader.feed(data)
        except Exception:
            grpc_reader.close()
    asyncio.create_task(forward())
    return grpc_reader,grpc_writer
