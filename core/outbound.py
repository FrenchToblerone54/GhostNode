#!/usr/bin/env python3.13
import asyncio
import base64
import logging
import socket
import struct

logger=logging.getLogger(__name__)

class BlockedError(Exception):
    pass

async def _open_tcp(host, port, sockopt=None, bind_address="", interface=""):
    if not sockopt and not bind_address and not interface:
        return await asyncio.open_connection(host, port)
    loop=asyncio.get_event_loop()
    infos=await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not infos:
        raise OSError(f"cannot resolve {host}")
    family,_,_,_,addr=infos[0]
    sock=socket.socket(family, socket.SOCK_STREAM)
    sock.setblocking(False)
    if sockopt:
        if sockopt.get("tcp_no_delay"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if sockopt.get("tcp_keep_alive"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if sockopt.get("tcp_fast_open"):
            _tfo=getattr(socket, "TCP_FASTOPEN_CONNECT", None)
            if _tfo:
                try: sock.setsockopt(socket.IPPROTO_TCP, _tfo, 1)
                except OSError: pass
        mark=int(sockopt.get("mark") or 0)
        if mark:
            _sm=getattr(socket, "SO_MARK", None)
            if _sm:
                try: sock.setsockopt(socket.SOL_SOCKET, _sm, mark)
                except OSError: pass
        cc=sockopt.get("tcp_congestion", "")
        if cc:
            _cc=getattr(socket, "TCP_CONGESTION", None)
            if _cc:
                try: sock.setsockopt(socket.IPPROTO_TCP, _cc, cc.encode())
                except OSError: pass
    if interface:
        _bdi=getattr(socket, "SO_BINDTODEVICE", None)
        if _bdi:
            try: sock.setsockopt(socket.SOL_SOCKET, _bdi, (interface+"\x00").encode())
            except OSError: pass
    if bind_address:
        sock.bind((bind_address, 0))
    try:
        await loop.sock_connect(sock, addr)
    except Exception:
        sock.close()
        raise
    return await asyncio.open_connection(sock=sock)

class Outbound:
    async def connect(self, addr, port, protocol="tcp"):
        raise NotImplementedError

class DirectOutbound(Outbound):
    def __init__(self, sockopt=None, bind_address="", interface="", redirect_address="", dialer_proxy_tag=""):
        self._sockopt=sockopt
        self._bind=bind_address
        self._interface=interface
        self._redirect=redirect_address
        self._dialer_proxy_tag=dialer_proxy_tag
        self._dialer=None

    async def connect(self, addr, port, protocol="tcp"):
        if self._dialer:
            _h,_p=addr,port
            if self._redirect:
                _rh,_,_rp=self._redirect.rpartition(":")
                _h,_p=_rh,(int(_rp) if _rp else port)
            return await self._dialer.connect(_h, _p, protocol)
        if protocol=="udp":
            loop=asyncio.get_event_loop()
            transport,protocol_obj=await loop.create_datagram_endpoint(asyncio.DatagramProtocol, remote_addr=(addr,port))
            return transport,protocol_obj
        if self._redirect:
            _rh,_,_rp=self._redirect.rpartition(":")
            addr,port=_rh,(int(_rp) if _rp else port)
        return await _open_tcp(addr, port, self._sockopt, self._bind, self._interface)

class BlockOutbound(Outbound):
    async def connect(self, addr, port, protocol="tcp"):
        raise BlockedError(f"blocked: {addr}:{port}")

class GhostNodeOutbound(Outbound):
    def __init__(self, url, nanoid, transport_type, path="/gn", sni="", allow_insecure=False, host_header="", extra_config=None):
        self._url=url
        self._nanoid=nanoid
        self._transport_type=transport_type
        self._path=path
        self._sni=sni
        self._allow_insecure=allow_insecure
        self._host=host_header
        self._extra=extra_config or {}

    async def connect(self, addr, port, protocol="tcp"):
        from core.protocol import encode_header,CMD_TCP,CMD_UDP,ADDR_IPV4,ADDR_DOMAIN,ADDR_IPV6
        try:
            socket.inet_aton(addr)
            addr_type=ADDR_IPV4
        except (socket.error, OSError):
            try:
                socket.inet_pton(socket.AF_INET6, addr)
                addr_type=ADDR_IPV6
            except (socket.error, OSError):
                addr_type=ADDR_DOMAIN
        cmd=CMD_UDP if protocol=="udp" else CMD_TCP
        header=encode_header(self._nanoid, cmd, addr_type, addr, port)
        reader,writer=await self._open_transport()
        writer.write(header)
        await writer.drain()
        return reader,writer

    async def _open_transport(self):
        h=self._host
        if self._transport_type=="websocket":
            from transport.websocket import connect as ws_connect
            return await ws_connect(self._url, self._path, self._sni, self._allow_insecure, h)
        elif self._transport_type=="http2":
            from transport.http2 import connect as h2_connect
            return await h2_connect(self._url, self._path, self._sni, self._allow_insecure, h)
        elif self._transport_type=="grpc":
            from transport.grpc import connect as grpc_connect
            return await grpc_connect(self._url, self._sni, self._allow_insecure, h)
        elif self._transport_type=="http-request":
            from transport.http_request import connect as hr_connect
            return await hr_connect(self._url, self._path, self._sni, self._allow_insecure, self._extra, h)
        elif self._transport_type=="http-request-sse":
            from transport.http_request_sse import connect as sse_connect
            return await sse_connect(self._url, self._path, self._sni, self._allow_insecure, self._extra, h)
        elif self._transport_type=="http-request-body":
            from transport.http_request_body import connect as hrb_connect
            return await hrb_connect(self._url, self._path, self._sni, self._allow_insecure, self._extra, h)
        raise ValueError(f"unknown transport: {self._transport_type}")

class Socks5Outbound(Outbound):
    def __init__(self, host, port, username="", password="", sockopt=None, bind_address="", interface="", dialer_proxy_tag=""):
        self._host=host
        self._port=port
        self._user=username
        self._pass=password
        self._sockopt=sockopt
        self._bind=bind_address
        self._interface=interface
        self._dialer_proxy_tag=dialer_proxy_tag
        self._dialer=None

    async def connect(self, addr, port, protocol="tcp"):
        if self._dialer:
            reader,writer=await self._dialer.connect(self._host, self._port)
        else:
            reader,writer=await _open_tcp(self._host, self._port, self._sockopt, self._bind, self._interface)
        try:
            if self._user and self._pass:
                writer.write(b"\x05\x02\x00\x02")
            else:
                writer.write(b"\x05\x01\x00")
            await writer.drain()
            resp=await reader.readexactly(2)
            if resp[0]!=0x05:
                raise ConnectionError("not a SOCKS5 server")
            method=resp[1]
            if method==0xFF:
                raise ConnectionError("SOCKS5 no acceptable auth method")
            if method==0x02:
                uenc=self._user.encode()
                penc=self._pass.encode()
                writer.write(bytes([0x01,len(uenc)])+uenc+bytes([len(penc)])+penc)
                await writer.drain()
                auth_resp=await reader.readexactly(2)
                if auth_resp[1]!=0x00:
                    raise ConnectionError("SOCKS5 auth failed")
            try:
                socket.inet_aton(addr)
                req=b"\x05\x01\x00\x01"+socket.inet_aton(addr)+struct.pack("!H", port)
            except (socket.error, OSError):
                try:
                    packed=socket.inet_pton(socket.AF_INET6, addr)
                    req=b"\x05\x01\x00\x04"+packed+struct.pack("!H", port)
                except (socket.error, OSError):
                    enc=addr.encode("utf-8")
                    req=b"\x05\x01\x00\x03"+bytes([len(enc)])+enc+struct.pack("!H", port)
            writer.write(req)
            await writer.drain()
            hdr=await reader.readexactly(4)
            if hdr[1]!=0x00:
                raise ConnectionError(f"SOCKS5 CONNECT failed: {hdr[1]}")
            atyp=hdr[3]
            if atyp==0x01:
                await reader.readexactly(4+2)
            elif atyp==0x03:
                dlen=(await reader.readexactly(1))[0]
                await reader.readexactly(dlen+2)
            elif atyp==0x04:
                await reader.readexactly(16+2)
            return reader,writer
        except Exception:
            writer.close()
            raise

class HTTPProxyOutbound(Outbound):
    def __init__(self, host, port, username="", password="", sockopt=None, bind_address="", interface="", dialer_proxy_tag=""):
        self._host=host
        self._port=port
        self._user=username
        self._pass=password
        self._sockopt=sockopt
        self._bind=bind_address
        self._interface=interface
        self._dialer_proxy_tag=dialer_proxy_tag
        self._dialer=None

    async def connect(self, addr, port, protocol="tcp"):
        if self._dialer:
            reader,writer=await self._dialer.connect(self._host, self._port)
        else:
            reader,writer=await _open_tcp(self._host, self._port, self._sockopt, self._bind, self._interface)
        try:
            target=f"{addr}:{port}"
            req=f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
            if self._user and self._pass:
                creds=base64.b64encode(f"{self._user}:{self._pass}".encode()).decode()
                req+=f"Proxy-Authorization: Basic {creds}\r\n"
            req+="\r\n"
            writer.write(req.encode())
            await writer.drain()
            resp=b""
            while b"\r\n\r\n" not in resp:
                chunk=await reader.read(1024)
                if not chunk:
                    raise ConnectionError("HTTP proxy closed connection")
                resp+=chunk
            status_line=resp.split(b"\r\n")[0].decode()
            code=int(status_line.split()[1])
            if code!=200:
                raise ConnectionError(f"HTTP proxy returned {code}")
            return reader,writer
        except Exception:
            writer.close()
            raise

def build_outbound(cfg):
    t=cfg.get("type", "direct")
    c=cfg.get("config", {})
    if isinstance(c, str):
        import json
        c=json.loads(c) if c else {}
    sockopt=c.get("sockopt") or {}
    bind_address=c.get("bind_address", "")
    interface=c.get("interface", "")
    redirect_address=c.get("redirect_address", "")
    dialer_proxy_tag=c.get("dialer_proxy", "")
    if t=="direct":
        return DirectOutbound(sockopt=sockopt, bind_address=bind_address, interface=interface, redirect_address=redirect_address, dialer_proxy_tag=dialer_proxy_tag)
    elif t=="block":
        return BlockOutbound()
    elif t=="socks5":
        return Socks5Outbound(c.get("host", ""), int(c.get("port", 1080)), c.get("username", ""), c.get("password", ""), sockopt=sockopt, bind_address=bind_address, interface=interface, dialer_proxy_tag=dialer_proxy_tag)
    elif t=="http":
        return HTTPProxyOutbound(c.get("host", ""), int(c.get("port", 8080)), c.get("username", ""), c.get("password", ""), sockopt=sockopt, bind_address=bind_address, interface=interface, dialer_proxy_tag=dialer_proxy_tag)
    elif t=="ghostnode":
        return GhostNodeOutbound(url=c.get("url", ""), nanoid=c.get("nanoid", ""), transport_type=c.get("transport", "websocket"), path=c.get("path", "/gn"), sni=c.get("sni", ""), allow_insecure=c.get("allow_insecure", False), host_header=c.get("host", ""), extra_config=c)
    return DirectOutbound()
