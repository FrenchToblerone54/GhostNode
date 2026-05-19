#!/usr/bin/env python3.13
import asyncio
import socket
import struct
import logging

logger=logging.getLogger(__name__)
RELAY_BUF=65536

async def _relay(reader, writer):
    try:
        while True:
            data=await reader.read(RELAY_BUF)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass

async def _socks5_handshake(reader, writer):
    nmethods=(await reader.readexactly(1))[0]
    await reader.readexactly(nmethods)
    writer.write(b"\x05\x00")
    await writer.drain()
    req=await reader.readexactly(4)
    if req[1]!=0x01:
        writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        return None, None
    atyp=req[3]
    if atyp==0x01:
        host=socket.inet_ntoa(await reader.readexactly(4))
    elif atyp==0x03:
        dlen=(await reader.readexactly(1))[0]
        host=(await reader.readexactly(dlen)).decode("utf-8","replace")
    elif atyp==0x04:
        host=socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
    else:
        return None, None
    port=struct.unpack("!H", await reader.readexactly(2))[0]
    writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
    await writer.drain()
    return host, port

async def _http_handshake(reader, writer, first_byte):
    buf=first_byte
    while b"\r\n\r\n" not in buf:
        chunk=await reader.read(4096)
        if not chunk:
            return None, None, b""
        buf+=chunk
    lines=buf.split(b"\r\n")
    parts=lines[0].decode("utf-8","replace").split(" ")
    if len(parts)<2:
        return None, None, b""
    method=parts[0]
    url=parts[1]
    if method=="CONNECT":
        colon=url.rfind(":")
        if colon!=-1:
            host=url[:colon]
            port=int(url[colon+1:])
        else:
            host=url
            port=443
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        return host, port, b""
    rest=url[7:] if url.startswith("http://") else url
    slash=rest.find("/")
    if slash==-1:
        host_part=rest
        path="/"
    else:
        host_part=rest[:slash]
        path=rest[slash:]
    colon=host_part.rfind(":")
    if colon!=-1:
        host=host_part[:colon]
        port=int(host_part[colon+1:])
    else:
        host=host_part
        port=80
    lines[0]=f"{method} {path} {parts[2] if len(parts)>2 else 'HTTP/1.1'}".encode()
    leftover=b"\r\n".join(lines)
    return host, port, leftover

async def handle_mixed_connection(reader, writer, inbound_tag, router, inb_cfg=None):
    from core.outbound import BlockedError, DirectOutbound
    cfg=inb_cfg or {}
    sockopt={}
    mark=int(cfg.get("sockopt_mark") or 0)
    if mark:
        sockopt["mark"]=mark
    if int(cfg.get("sockopt_tcp_fast_open") or 0):
        sockopt["tcp_fast_open"]=True
    if int(cfg.get("sockopt_tcp_no_delay") or 0):
        sockopt["tcp_no_delay"]=True
    if int(cfg.get("sockopt_tcp_keep_alive") or 0):
        sockopt["tcp_keep_alive"]=True
    cc=cfg.get("sockopt_tcp_congestion") or ""
    if cc:
        sockopt["tcp_congestion"]=cc
    leftover=b""
    try:
        first=await reader.read(1)
        if not first:
            writer.close()
            return
        if first[0]==0x05:
            host, port=await _socks5_handshake(reader, writer)
        else:
            host, port, leftover=await _http_handshake(reader, writer, first)
        if not host:
            writer.close()
            return
        try:
            socket.inet_aton(host)
            ip=host
            domain=""
        except (socket.error, OSError):
            try:
                socket.inet_pton(socket.AF_INET6, host)
                ip=host
                domain=""
            except (socket.error, OSError):
                domain=host
                try:
                    infos=await asyncio.get_event_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
                    ip=infos[0][4][0] if infos else ""
                except Exception:
                    ip=""
        conn_info={"domain":domain,"ip":ip,"port":port,"protocol":"tcp","inbound_tag":inbound_tag,"user":""}
        outbound_tag=router.match(conn_info)
        if outbound_tag=="direct" and sockopt:
            outbound=DirectOutbound(sockopt=sockopt)
        else:
            outbound=router._outbounds.get(outbound_tag, router._outbounds.get("direct"))
        try:
            r2, w2=await outbound.connect(ip or host, port, "tcp")
        except BlockedError:
            writer.close()
            return
        except Exception as e:
            logger.debug(f"mixed outbound connect failed: {e}")
            writer.close()
            return
        if leftover:
            w2.write(leftover)
            await w2.drain()
        await asyncio.gather(_relay(reader, w2), _relay(r2, writer), return_exceptions=True)
    except Exception as e:
        logger.debug(f"handle_mixed_connection error: {e}")
    finally:
        try:
            writer.close()
        except Exception:
            pass
