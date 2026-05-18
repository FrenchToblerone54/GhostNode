#!/usr/bin/env python3.13
import asyncio
import ssl as ssl_mod
import logging
from aiohttp import web
from aiohttp.web import TCPSite

logger=logging.getLogger(__name__)

class PortMux:
    def __init__(self, listen_ip, port, ssl_cert="", ssl_key=""):
        self._listen_ip=listen_ip
        self._port=port
        self._ssl_cert=ssl_cert
        self._ssl_key=ssl_key
        self._handlers={}
        self._runner=None

    def add(self, path, handler_fn):
        self._handlers[path]=handler_fn

    def remove(self, path):
        self._handlers.pop(path, None)

    def empty(self):
        return not self._handlers

    async def start(self):
        ssl_ctx=None
        if self._ssl_cert and self._ssl_key:
            ssl_ctx=ssl_mod.create_default_context(ssl_mod.Purpose.CLIENT_AUTH)
            ssl_ctx.load_cert_chain(self._ssl_cert, self._ssl_key)
        app=web.Application()
        mux=self
        async def dispatch(request):
            path=request.path
            best=None
            for prefix in mux._handlers:
                if path==prefix or path.startswith(prefix+"/"):
                    if best is None or len(prefix)>len(best):
                        best=prefix
            if best:
                return await mux._handlers[best](request)
            return web.Response(status=404)
        app.router.add_route("*", "/{tail:.*}", dispatch)
        self._runner=web.AppRunner(app)
        await self._runner.setup()
        site=TCPSite(self._runner, self._listen_ip, self._port, ssl_context=ssl_ctx)
        await site.start()
        logger.info(f"port mux on {self._listen_ip}:{self._port}")

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()
            self._runner=None
            logger.info(f"port mux stopped on {self._listen_ip}:{self._port}")
