#!/usr/bin/env python3.13

def sniff_protocol(data: bytes, is_udp: bool) -> str:
    if not data:
        return ""
    if is_udp:
        if len(data)>=5 and (data[0] & 0x80):
            version=int.from_bytes(data[1:5], "big")
            if version in (0x00000001, 0x00000000, 0xff000020, 0xff000021, 0xff000022, 0x6b3343cf):
                return "quic"
        return ""
    if len(data)>=20 and data[0]==0x13 and data[1:20]==b"BitTorrent protocol":
        return "bittorrent"
    return ""
