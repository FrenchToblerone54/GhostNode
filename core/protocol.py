#!/usr/bin/env python3.13
import struct
import socket
from nanoid import generate

CMD_TCP=0x01
CMD_UDP=0x02
CMD_MUX=0x03
ADDR_IPV4=0x01
ADDR_DOMAIN=0x02
ADDR_IPV6=0x03
VERSION=0x01
NANOID_LEN=20

def generate_nanoid():
    return generate(size=NANOID_LEN)

def encode_header(nanoid, command, addr_type, addr, port):
    nanoid_bytes=nanoid.encode("ascii")[:NANOID_LEN].ljust(NANOID_LEN,b"\x00")
    hdr=bytes([VERSION,NANOID_LEN])+nanoid_bytes+bytes([command,addr_type])
    if addr_type==ADDR_IPV4:
        hdr+=socket.inet_aton(addr)
    elif addr_type==ADDR_IPV6:
        hdr+=socket.inet_pton(socket.AF_INET6,addr)
    elif addr_type==ADDR_DOMAIN:
        dom=addr.encode("utf-8")
        hdr+=bytes([len(dom)])+dom
    hdr+=struct.pack("!H",port)
    return hdr

def decode_header(data):
    if len(data)<4:
        return None
    version=data[0]
    id_len=data[1]
    if len(data)<2+id_len+4:
        return None
    nanoid=data[2:2+id_len].decode("ascii").rstrip("\x00")
    offset=2+id_len
    command=data[offset]
    addr_type=data[offset+1]
    offset+=2
    addr=None
    if addr_type==ADDR_IPV4:
        if len(data)<offset+4+2:
            return None
        addr=socket.inet_ntoa(data[offset:offset+4])
        offset+=4
    elif addr_type==ADDR_IPV6:
        if len(data)<offset+16+2:
            return None
        addr=socket.inet_ntop(socket.AF_INET6,data[offset:offset+16])
        offset+=16
    elif addr_type==ADDR_DOMAIN:
        if len(data)<offset+1:
            return None
        dom_len=data[offset]
        offset+=1
        if len(data)<offset+dom_len+2:
            return None
        addr=data[offset:offset+dom_len].decode("utf-8")
        offset+=dom_len
    if len(data)<offset+2:
        return None
    port=struct.unpack("!H",data[offset:offset+2])[0]
    offset+=2
    return {"version":version,"nanoid":nanoid,"command":command,"addr_type":addr_type,"addr":addr,"port":port,"bytes_consumed":offset}
