#!/usr/bin/env python3.13
import asyncio
import struct
import logging

logger=logging.getLogger(__name__)

FLAG_NEW=0x01
FLAG_DATA=0x02
FLAG_FIN=0x04
FLAG_RST=0x08
FRAME_HEADER_SIZE=9

class MuxStream:
    def __init__(self, stream_id, session):
        self.stream_id=stream_id
        self._session=session
        self._buf=bytearray()
        self._data_event=asyncio.Event()
        self._closed=False
        self._remote_closed=False

    async def read(self, n=-1):
        while not self._buf and not self._remote_closed and not self._closed:
            self._data_event.clear()
            await self._data_event.wait()
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
        while len(self._buf)<n and not self._remote_closed and not self._closed:
            self._data_event.clear()
            await self._data_event.wait()
        if len(self._buf)<n:
            raise asyncio.IncompleteReadError(bytes(self._buf),n)
        data=bytes(self._buf[:n])
        del self._buf[:n]
        return data

    async def write(self, data):
        if self._closed:
            return
        await self._session._send_frame(self.stream_id,FLAG_DATA,data)

    def feed(self, data):
        self._buf.extend(data)
        self._data_event.set()

    def mark_remote_closed(self):
        self._remote_closed=True
        self._data_event.set()

    async def close(self):
        if self._closed:
            return
        self._closed=True
        await self._session._send_frame(self.stream_id,FLAG_FIN,b"")
        self._session._remove_stream(self.stream_id)

    async def rst(self):
        if self._closed:
            return
        self._closed=True
        await self._session._send_frame(self.stream_id,FLAG_RST,b"")
        self._session._remove_stream(self.stream_id)

class MuxSession:
    def __init__(self, reader, writer):
        self._reader=reader
        self._writer=writer
        self._streams={}
        self._next_id=1
        self._accept_queue=asyncio.Queue()
        self._lock=asyncio.Lock()
        self._closed=False

    async def _send_frame(self, stream_id, flags, data):
        frame=struct.pack("!IBI",stream_id,flags,len(data))+data
        async with self._lock:
            self._writer.write(frame)
            await self._writer.drain()

    def _remove_stream(self, stream_id):
        self._streams.pop(stream_id,None)

    async def open_stream(self):
        stream_id=self._next_id
        self._next_id+=2
        stream=MuxStream(stream_id,self)
        self._streams[stream_id]=stream
        await self._send_frame(stream_id,FLAG_NEW,b"")
        return stream

    async def accept_stream(self):
        return await self._accept_queue.get()

    async def close_all(self):
        self._closed=True
        for stream in list(self._streams.values()):
            stream.mark_remote_closed()
        self._streams.clear()

    async def run(self):
        try:
            while not self._closed:
                header=await self._reader.readexactly(FRAME_HEADER_SIZE)
                stream_id,flags,length=struct.unpack("!IBI",header)
                data=await self._reader.readexactly(length) if length>0 else b""
                if flags&FLAG_NEW:
                    stream=MuxStream(stream_id,self)
                    self._streams[stream_id]=stream
                    await self._accept_queue.put(stream)
                elif flags&FLAG_DATA:
                    stream=self._streams.get(stream_id)
                    if stream:
                        stream.feed(data)
                elif flags&FLAG_FIN:
                    stream=self._streams.get(stream_id)
                    if stream:
                        stream.mark_remote_closed()
                    self._streams.pop(stream_id,None)
                elif flags&FLAG_RST:
                    stream=self._streams.get(stream_id)
                    if stream:
                        stream.mark_remote_closed()
                    self._streams.pop(stream_id,None)
        except (asyncio.IncompleteReadError,ConnectionResetError,BrokenPipeError):
            pass
        except Exception as e:
            logger.debug(f"mux run error: {e}")
        finally:
            await self.close_all()
