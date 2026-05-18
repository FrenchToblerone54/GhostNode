#!/usr/bin/env python3.13
import asyncio
import logging
import struct
import socket

logger=logging.getLogger(__name__)

SOCKS5_VERSION=0x05
AUTH_NONE=0x00
CMD_CONNECT=0x01
ADDR_IPV4=0x01
ADDR_DOMAIN=0x03
ADDR_IPV6=0x04
REP_SUCCESS=0x00
REP_FAILURE=0x01

async def _send_reply(writer, rep, addr="0.0.0.0", port=0):
    writer.write(bytes([SOCKS5_VERSION,rep,0x00,ADDR_IPV4])+socket.inet_aton(addr)+struct.pack("!H",port))
    await writer.drain()

async def handle_socks5(reader, writer, connect_fn, stats=None):
    if stats is not None:
        stats["active"]+=1
        stats["total"]+=1
    try:
        ver_nmethods=await reader.readexactly(2)
        if ver_nmethods[0]!=SOCKS5_VERSION:
            writer.close()
            return
        nmethods=ver_nmethods[1]
        methods=await reader.readexactly(nmethods)
        if AUTH_NONE not in methods:
            writer.write(bytes([SOCKS5_VERSION,0xFF]))
            await writer.drain()
            writer.close()
            return
        writer.write(bytes([SOCKS5_VERSION,AUTH_NONE]))
        await writer.drain()
        req=await reader.readexactly(4)
        if req[0]!=SOCKS5_VERSION or req[1]!=CMD_CONNECT:
            await _send_reply(writer,REP_FAILURE)
            writer.close()
            return
        atyp=req[3]
        if atyp==ADDR_IPV4:
            addr_bytes=await reader.readexactly(4)
            target_addr=socket.inet_ntoa(addr_bytes)
        elif atyp==ADDR_DOMAIN:
            dlen=(await reader.readexactly(1))[0]
            target_addr=(await reader.readexactly(dlen)).decode("utf-8")
        elif atyp==ADDR_IPV6:
            addr_bytes=await reader.readexactly(16)
            target_addr=socket.inet_ntop(socket.AF_INET6,addr_bytes)
        else:
            await _send_reply(writer,REP_FAILURE)
            writer.close()
            return
        port_bytes=await reader.readexactly(2)
        target_port=struct.unpack("!H",port_bytes)[0]
        try:
            remote_reader,remote_writer=await connect_fn(target_addr,target_port,atyp)
        except Exception as e:
            logger.debug(f"socks5 connect failed: {e}")
            await _send_reply(writer,REP_FAILURE)
            writer.close()
            return
        await _send_reply(writer,REP_SUCCESS)
        async def pipe(r,w,key):
            try:
                while True:
                    data=await r.read(65536)
                    if not data:
                        break
                    if stats is not None:
                        stats[key]+=len(data)
                    w.write(data)
                    await w.drain()
            except Exception:
                pass
            finally:
                try:
                    w.close()
                except Exception:
                    pass
        await asyncio.gather(pipe(reader,remote_writer,"sent"),pipe(remote_reader,writer,"recv"),return_exceptions=True)
    except Exception as e:
        logger.debug(f"socks5 handler error: {e}")
    finally:
        if stats is not None:
            stats["active"]-=1
        try:
            writer.close()
        except Exception:
            pass

async def start_socks5_server(host, port, connect_fn, stats=None):
    server=await asyncio.start_server(lambda r,w: handle_socks5(r,w,connect_fn,stats),host,port)
    logger.info(f"SOCKS5 listening on {host}:{port}")
    return server
