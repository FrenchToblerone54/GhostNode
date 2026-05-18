#!/usr/bin/env python3.13
import asyncio
import logging
from core.protocol import encode_header, decode_header, CMD_MUX, CMD_TCP, CMD_CFG, ADDR_IPV4, ADDR_DOMAIN

logger=logging.getLogger(__name__)

_pools={}

def get_pool(cfg):
    key=(cfg["nanoid"],cfg["url"])
    if key not in _pools:
        _pools[key]=MuxPool(cfg)
    return _pools[key]


class _MuxStreamWriter:
    def __init__(self, stream):
        self._stream=stream
        self._buf=bytearray()

    def write(self, data):
        self._buf.extend(data)

    async def drain(self):
        if self._buf:
            await self._stream.write(bytes(self._buf))
            self._buf.clear()

    def close(self):
        asyncio.ensure_future(self._stream.close())


class MuxPool:
    def __init__(self, cfg):
        self._cfg=cfg
        self._size=cfg.get("pool_size", 8)
        self._sessions={}
        self._started=False
        self._workers={}
        self._shutdown=None

    def _ensure_started(self):
        if not self._started:
            self._started=True
            self._shutdown=asyncio.Event()
            for slot in range(self._size):
                self._workers[slot]=asyncio.create_task(self._slot_worker(slot))

    def _pick(self):
        live=[(sid, s) for sid, s in self._sessions.items() if not s["dead"]]
        if not live:
            return None
        return min(live, key=lambda x: x[1]["active"])[0]

    async def open_stream(self, target_addr, target_port, addr_type):
        self._ensure_started()
        sid=self._pick()
        if sid is None:
            return None
        slot=self._sessions[sid]
        slot["active"]+=1
        try:
            stream=await slot["session"].open_stream()
            header=encode_header(self._cfg["nanoid"], CMD_TCP, addr_type, target_addr, target_port)
            await stream.write(header)
            orig_close=stream.close
            orig_rst=stream.rst
            async def _close():
                slot["active"]=max(0, slot["active"]-1)
                await orig_close()
            async def _rst():
                slot["active"]=max(0, slot["active"]-1)
                await orig_rst()
            stream.close=_close
            stream.rst=_rst
            return stream, _MuxStreamWriter(stream)
        except Exception:
            slot["active"]=max(0, slot["active"]-1)
            slot["dead"]=True
            return None

    async def _connect_session(self, slot_id):
        from client.connector import open_transport
        from core.crypto import client_handshake
        from core.mux import MuxSession
        nanoid=self._cfg["nanoid"]
        fp=self._cfg.get("fp", "")
        reader, writer=await open_transport(self._cfg)
        reader, writer=await client_handshake(reader, writer, nanoid, fp)
        import json as _json
        _raw=await reader.read(512)
        if _raw:
            _hdr=decode_header(_raw)
            if _hdr and _hdr["command"]==CMD_CFG:
                try:
                    _srv=_json.loads(_hdr["addr"])
                    if "ps" in _srv:
                        self._cfg["pool_size"]=int(_srv["ps"])
                        self._size=int(_srv["ps"])
                    if "pc" in _srv: self._cfg["poll_connections"]=int(_srv["pc"])
                    if "pi" in _srv: self._cfg["ping_interval"]=int(_srv["pi"])
                    if "pt" in _srv: self._cfg["ping_timeout"]=int(_srv["pt"])
                    if "ua" in _srv and _srv["ua"]: self._cfg["user_agent"]=_srv["ua"]
                except Exception:
                    pass
        _cfg_str=_json.dumps({"ps":self._size})
        _cfg_hdr=encode_header(nanoid, CMD_CFG, ADDR_DOMAIN, _cfg_str, 0)
        writer.write(_cfg_hdr)
        mux_header=encode_header(nanoid, CMD_MUX, ADDR_IPV4, "0.0.0.0", 0)
        writer.write(mux_header)
        await writer.drain()
        session=MuxSession(reader, writer)
        sid=f"s{slot_id}"
        self._sessions[sid]={"session": session, "active": 0, "dead": False}
        asyncio.create_task(self._watch_session(sid, session))
        logger.debug(f"pool slot {slot_id} connected")
        return sid

    async def _watch_session(self, sid, session):
        try:
            await session.run()
        finally:
            if sid in self._sessions:
                self._sessions[sid]["dead"]=True

    async def _slot_worker(self, slot_id):
        delay=1
        while not self._shutdown.is_set():
            try:
                await self._connect_session(slot_id)
                delay=1
                sid=f"s{slot_id}"
                while not self._shutdown.is_set():
                    slot=self._sessions.get(sid)
                    if not slot or slot["dead"]:
                        break
                    await asyncio.sleep(0.5)
            except Exception as e:
                logger.debug(f"pool slot {slot_id} connect failed: {e}")
            if self._shutdown.is_set():
                break
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=min(delay, 60))
                break
            except asyncio.TimeoutError:
                pass
            delay=min(delay*2, 60)

    def stop(self):
        if self._shutdown:
            self._shutdown.set()
        for t in self._workers.values():
            t.cancel()
        self._workers.clear()
        self._sessions.clear()
        self._started=False
