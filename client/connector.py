#!/usr/bin/env python3.13
import asyncio
import logging
import socket
from core.protocol import encode_header,decode_header,CMD_TCP,CMD_CFG,ADDR_IPV4,ADDR_DOMAIN,ADDR_IPV6

logger=logging.getLogger(__name__)

async def open_transport(cfg):
    transport=cfg.get("transport","ws")
    url=cfg.get("url","")
    path=cfg.get("path","/gn")
    sni=cfg.get("sni","")
    allow_insecure=cfg.get("allow_insecure",False)
    if transport in ("ws","websocket"):
        from transport.websocket import connect as ws_connect
        return await ws_connect(url,path,sni,allow_insecure,extra=cfg)
    elif transport in ("h2","http2"):
        from transport.http2 import connect as h2_connect
        return await h2_connect(url,path,sni,allow_insecure)
    elif transport=="grpc":
        from transport.grpc import connect as grpc_connect
        return await grpc_connect(url,sni,allow_insecure)
    elif transport in ("hr","http-request"):
        from transport.http_request import connect as hr_connect
        return await hr_connect(url,path,sni,allow_insecure,extra=cfg)
    elif transport in ("sse","http-request-sse"):
        from transport.http_request_sse import connect as sse_connect
        return await sse_connect(url,path,sni,allow_insecure,extra=cfg)
    elif transport in ("hrb","http-request-body"):
        from transport.http_request_body import connect as hrb_connect
        return await hrb_connect(url,path,sni,allow_insecure,extra=cfg)
    raise ValueError(f"unknown transport: {transport}")

def _addr_type_from_socks(atyp):
    if atyp==0x01:
        return ADDR_IPV4
    elif atyp==0x03:
        return ADDR_DOMAIN
    elif atyp==0x04:
        return ADDR_IPV6
    return ADDR_DOMAIN

async def connect_to_server(cfg, target_addr, target_port, socks_atyp=0x03):
    addr_type=_addr_type_from_socks(socks_atyp)
    if cfg.get("pool_size", 8)>0:
        from client.pool import get_pool
        result=await get_pool(cfg).open_stream(target_addr, target_port, addr_type)
        if result:
            return result
    nanoid=cfg["nanoid"]
    header=encode_header(nanoid,CMD_TCP,addr_type,target_addr,target_port)
    reader,writer=await open_transport(cfg)
    from core.crypto import client_handshake
    reader,writer=await client_handshake(reader,writer,nanoid,cfg.get("fp",""))
    _raw=await reader.read(512)
    if _raw:
        _hdr=decode_header(_raw)
        if _hdr and _hdr["command"]==CMD_CFG:
            try:
                import json as _json
                _srv=_json.loads(_hdr["addr"])
                if "ps" in _srv: cfg["pool_size"]=int(_srv["ps"])
                if "pc" in _srv: cfg["poll_connections"]=int(_srv["pc"])
                if "pi" in _srv: cfg["ping_interval"]=int(_srv["pi"])
                if "pt" in _srv: cfg["ping_timeout"]=int(_srv["pt"])
                if "ua" in _srv and _srv["ua"]: cfg["user_agent"]=_srv["ua"]
            except Exception:
                pass
    writer.write(header)
    await writer.drain()
    return reader,writer
