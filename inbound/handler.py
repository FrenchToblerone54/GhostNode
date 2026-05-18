#!/usr/bin/env python3.13
import asyncio
import logging
import socket
from core.protocol_sniff import sniff_protocol

logger=logging.getLogger(__name__)
RELAY_BUF=65536

async def _relay(reader, writer, on_bytes=None):
    try:
        while True:
            data=await reader.read(RELAY_BUF)
            if not data:
                break
            writer.write(data)
            await writer.drain()
            if on_bytes:
                await on_bytes(len(data))
    except (asyncio.IncompleteReadError,ConnectionResetError,BrokenPipeError,OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass

async def _relay_bidirectional(reader_a, writer_a, reader_b, writer_b, nanoid, traffic_ctrl, upload_to_b=True):
    async def up(r,w):
        try:
            while True:
                data=await r.read(RELAY_BUF)
                if not data:
                    break
                w.write(data)
                await w.drain()
                if upload_to_b:
                    await traffic_ctrl.record_bytes(nanoid,len(data),0)
                else:
                    await traffic_ctrl.record_bytes(nanoid,0,len(data))
        except Exception:
            pass
        finally:
            try:
                w.close()
            except Exception:
                pass

    async def dn(r,w):
        try:
            while True:
                data=await r.read(RELAY_BUF)
                if not data:
                    break
                w.write(data)
                await w.drain()
                if upload_to_b:
                    await traffic_ctrl.record_bytes(nanoid,0,len(data))
                else:
                    await traffic_ctrl.record_bytes(nanoid,len(data),0)
        except Exception:
            pass
        finally:
            try:
                w.close()
            except Exception:
                pass

    await asyncio.gather(up(reader_a,writer_b),dn(reader_b,writer_a),return_exceptions=True)

async def _resolve_addr(addr, addr_type):
    from core.protocol import ADDR_IPV4,ADDR_IPV6,ADDR_DOMAIN
    if addr_type in (ADDR_IPV4,ADDR_IPV6):
        return addr
    try:
        infos=await asyncio.get_event_loop().getaddrinfo(addr,None,type=socket.SOCK_STREAM)
        return infos[0][4][0]
    except Exception:
        return addr

async def handle_connection(reader, writer, inbound_tag, db, router, traffic_ctrl):
    from core.protocol import decode_header,CMD_TCP,CMD_UDP,CMD_MUX,ADDR_DOMAIN
    from core.outbound import BlockedError
    try:
        from core.crypto import server_handshake
        try:
            reader,writer,_=await server_handshake(reader,writer,db)
        except Exception as e:
            logger.debug(f"crypto handshake failed: {e}")
            try:
                writer.close()
            except Exception:
                pass
            return
        raw=await reader.read(512)
        if not raw:
            writer.close()
            return
        hdr=decode_header(raw)
        if not hdr:
            writer.close()
            return
        nanoid=hdr["nanoid"]
        command=hdr["command"]
        addr=hdr["addr"]
        port=hdr["port"]
        addr_type=hdr["addr_type"]
        leftover=raw[hdr["bytes_consumed"]:]
        _sniffed=sniff_protocol(leftover, command==CMD_UDP)
        allowed=await traffic_ctrl.check_client(nanoid)
        if not allowed:
            writer.close()
            return
        if command==CMD_MUX:
            await _handle_mux(reader,writer,leftover,nanoid,inbound_tag,router,traffic_ctrl,addr_type)
            return
        ip=await _resolve_addr(addr,addr_type)
        domain=addr if addr_type==ADDR_DOMAIN else ""
        conn_info={"domain":domain,"ip":ip,"port":port,"protocol":_sniffed if _sniffed else ("udp" if command==CMD_UDP else "tcp"),"inbound_tag":inbound_tag,"user":nanoid}
        outbound_tag=router.match(conn_info)
        outbounds=router._outbounds
        outbound=outbounds.get(outbound_tag,outbounds.get("direct"))
        try:
            r2,w2=await outbound.connect(ip,port,"udp" if command==CMD_UDP else "tcp")
        except BlockedError:
            writer.close()
            return
        except Exception as e:
            logger.debug(f"outbound connect failed: {e}")
            writer.close()
            return
        traffic_ctrl.register_stream(nanoid,writer)
        if leftover:
            w2.write(leftover)
            await w2.drain()
        try:
            await _relay_bidirectional(reader,writer,r2,w2,nanoid,traffic_ctrl)
        finally:
            traffic_ctrl.unregister_stream(nanoid,writer)
    except Exception as e:
        logger.debug(f"handle_connection error: {e}")
    finally:
        try:
            writer.close()
        except Exception:
            pass

async def _handle_mux(reader, writer, leftover, nanoid, inbound_tag, router, traffic_ctrl, _addr_type):
    from core.mux import MuxSession
    from core.protocol import decode_header,CMD_TCP,ADDR_DOMAIN
    from core.outbound import BlockedError

    class _PrefixReader:
        def __init__(self, prefix, base):
            self._prefix=bytearray(prefix)
            self._base=base
        async def readexactly(self, n):
            if len(self._prefix)>=n:
                data=bytes(self._prefix[:n])
                del self._prefix[:n]
                return data
            need=n-len(self._prefix)
            extra=await self._base.readexactly(need)
            data=bytes(self._prefix)+extra
            self._prefix.clear()
            return data
        async def read(self, n):
            if self._prefix:
                data=bytes(self._prefix[:n])
                del self._prefix[:n]
                return data
            return await self._base.read(n)

    prefixed=_PrefixReader(leftover,reader)
    session=MuxSession(prefixed,writer)
    traffic_ctrl.register_stream(nanoid,writer)

    async def handle_stream(stream):
        try:
            raw=await stream.read(512)
            if not raw:
                await stream.close()
                return
            hdr=decode_header(raw)
            if not hdr:
                await stream.close()
                return
            sub_nanoid=hdr["nanoid"]
            allowed=await traffic_ctrl.check_client(sub_nanoid)
            if not allowed:
                await stream.rst()
                return
            addr=hdr["addr"]
            port=hdr["port"]
            addr_type=hdr["addr_type"]
            leftover2=raw[hdr["bytes_consumed"]:]
            _sniffed2=sniff_protocol(leftover2, False)
            ip=await _resolve_addr(addr,addr_type)
            domain=addr if addr_type==ADDR_DOMAIN else ""
            conn_info={"domain":domain,"ip":ip,"port":port,"protocol":_sniffed2 if _sniffed2 else "tcp","inbound_tag":inbound_tag,"user":sub_nanoid}
            outbound_tag=router.match(conn_info)
            outbound=router._outbounds.get(outbound_tag,router._outbounds.get("direct"))
            try:
                r2,w2=await outbound.connect(ip,port,"tcp")
            except BlockedError:
                await stream.rst()
                return
            except Exception as e:
                logger.debug(f"mux stream connect failed: {e}")
                await stream.rst()
                return
            traffic_ctrl.register_stream(sub_nanoid,stream)
            if leftover2:
                w2.write(leftover2)
                await w2.drain()
            async def up():
                try:
                    while True:
                        data=await stream.read(RELAY_BUF)
                        if not data:
                            break
                        w2.write(data)
                        await w2.drain()
                        await traffic_ctrl.record_bytes(sub_nanoid,len(data),0)
                except Exception:
                    pass
                finally:
                    w2.close()

            async def dn():
                try:
                    while True:
                        data=await r2.read(RELAY_BUF)
                        if not data:
                            break
                        await stream.write(data)
                        await traffic_ctrl.record_bytes(sub_nanoid,0,len(data))
                except Exception:
                    pass
                finally:
                    await stream.close()

            await asyncio.gather(up(),dn(),return_exceptions=True)
            traffic_ctrl.unregister_stream(sub_nanoid,stream)
        except Exception as e:
            logger.debug(f"mux stream error: {e}")

    async def accept_loop():
        while True:
            try:
                stream=await session.accept_stream()
                asyncio.create_task(handle_stream(stream))
            except Exception:
                break

    try:
        await asyncio.gather(session.run(),accept_loop(),return_exceptions=True)
    finally:
        traffic_ctrl.unregister_stream(nanoid,writer)
