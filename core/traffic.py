#!/usr/bin/env python3.13
import asyncio
import logging
from datetime import datetime, timezone

logger=logging.getLogger(__name__)

class TrafficController:
    def __init__(self, db):
        self._db=db
        self._locks={}
        self._streams={}
        self._pending_upload={}
        self._pending_download={}
        self._flush_interval=5
        self._xray=None

    def set_xray(self, xm):
        self._xray=xm

    def _get_lock(self, nanoid):
        if nanoid not in self._locks:
            self._locks[nanoid]=asyncio.Lock()
        return self._locks[nanoid]

    async def check_client(self, nanoid):
        client=await self._db.get_client_by_nanoid(nanoid)
        if not client:
            return False
        if not client["enabled"]:
            return False
        if client["expire_date"]:
            try:
                expire=datetime.fromisoformat(client["expire_date"])
                if expire.tzinfo is None:
                    expire=expire.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc)>expire:
                    await self._db.disable_client(client["id"])
                    if self._xray:
                        asyncio.ensure_future(self._xray.restart())
                    return False
            except ValueError:
                pass
        if client["traffic_limit"]>0 and (client["upload"]+client["download"])>=client["traffic_limit"]:
            await self._db.disable_client(client["id"])
            if self._xray:
                asyncio.ensure_future(self._xray.restart())
            return False
        return True

    async def record_bytes(self, nanoid, upload, download):
        async with self._get_lock(nanoid):
            self._pending_upload[nanoid]=self._pending_upload.get(nanoid,0)+upload
            self._pending_download[nanoid]=self._pending_download.get(nanoid,0)+download

    def register_stream(self, nanoid, stream):
        if nanoid not in self._streams:
            self._streams[nanoid]=set()
        self._streams[nanoid].add(stream)

    def unregister_stream(self, nanoid, stream):
        if nanoid in self._streams:
            self._streams[nanoid].discard(stream)

    async def kill_client_streams(self, nanoid):
        streams=self._streams.get(nanoid,set()).copy()
        for stream in streams:
            try:
                if hasattr(stream,"close"):
                    stream.close()
            except Exception:
                pass
        self._streams.pop(nanoid,None)

    async def _flush_loop(self):
        while True:
            await asyncio.sleep(self._flush_interval)
            await self._flush_pending()

    async def _flush_pending(self):
        items=list(self._pending_upload.keys())
        for nanoid in items:
            up=self._pending_upload.pop(nanoid,0)
            dn=self._pending_download.pop(nanoid,0)
            if up>0 or dn>0:
                await self._db.add_traffic(nanoid,up,dn)
                client=await self._db.get_client_by_nanoid(nanoid)
                if client and client["traffic_limit"]>0:
                    if (client["upload"]+client["download"])>=client["traffic_limit"]:
                        await self._db.disable_client(client["id"])
                        if self._xray:
                            asyncio.ensure_future(self._xray.restart())
                        await self.kill_client_streams(nanoid)
                        logger.info(f"client {nanoid} traffic limit reached, connections killed")

    async def start(self):
        asyncio.create_task(self._flush_loop())
