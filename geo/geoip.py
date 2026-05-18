#!/usr/bin/env python3.13
import ipaddress
import logging
import struct

logger=logging.getLogger(__name__)

def _read_varint(data, pos):
    result=0
    shift=0
    while pos<len(data):
        b=data[pos]
        pos+=1
        result|=(b&0x7F)<<shift
        if not (b&0x80):
            break
        shift+=7
    return result,pos

def _read_field(data, pos):
    if pos>=len(data):
        return None,0,None,pos
    tag,pos=_read_varint(data,pos)
    field_num=tag>>3
    wire_type=tag&0x07
    if wire_type==0:
        val,pos=_read_varint(data,pos)
        return field_num,0,val,pos
    elif wire_type==2:
        length,pos=_read_varint(data,pos)
        val=data[pos:pos+length]
        return field_num,2,val,pos+length
    return None,wire_type,None,pos+1

def _parse_cidr(data):
    ip_bytes=b""
    prefix=0
    pos=0
    while pos<len(data):
        fn,wt,val,pos=_read_field(data,pos)
        if fn is None:
            break
        if fn==1 and wt==2:
            ip_bytes=val
        elif fn==2 and wt==0:
            prefix=val
    return ip_bytes,prefix

def _parse_geoip_entry(data):
    code=""
    cidrs=[]
    pos=0
    while pos<len(data):
        fn,wt,val,pos=_read_field(data,pos)
        if fn is None:
            break
        if fn==1 and wt==2:
            code=val.decode("utf-8","ignore").upper()
        elif fn==2 and wt==2:
            ip_b,prefix=_parse_cidr(val)
            if ip_b:
                cidrs.append((ip_b,prefix))
    return code,cidrs

class GeoIPDat:
    def __init__(self, dat_path):
        self._path=dat_path
        self._data={}

    def load(self):
        try:
            with open(self._path,"rb") as f:
                raw=f.read()
            pos=0
            while pos<len(raw):
                fn,wt,val,pos=_read_field(raw,pos)
                if fn is None:
                    break
                if fn==1 and wt==2:
                    code,cidrs=_parse_geoip_entry(val)
                    if code:
                        networks=[]
                        for ip_b,prefix in cidrs:
                            try:
                                if len(ip_b)==4:
                                    addr=ipaddress.IPv4Address(ip_b)
                                    networks.append(ipaddress.IPv4Network(f"{addr}/{prefix}",strict=False))
                                elif len(ip_b)==16:
                                    addr=ipaddress.IPv6Address(ip_b)
                                    networks.append(ipaddress.IPv6Network(f"{addr}/{prefix}",strict=False))
                            except Exception:
                                pass
                        self._data[code]=networks
            logger.info(f"geoip.dat loaded {len(self._data)} countries from {self._path}")
        except FileNotFoundError:
            logger.warning(f"geoip.dat not found: {self._path}")
        except Exception as e:
            logger.warning(f"geoip.dat load error: {e}")

    def country_code(self, ip):
        try:
            addr=ipaddress.ip_address(ip)
        except ValueError:
            return ""
        for code,networks in self._data.items():
            for net in networks:
                if addr in net:
                    return code
        return ""

    def matches(self, ip, code):
        if not self._data:
            return False
        try:
            addr=ipaddress.ip_address(ip)
        except ValueError:
            return False
        for net in self._data.get(code.upper(),[]):
            if addr in net:
                return True
        return False

class GeoIP:
    def __init__(self, mmdb_path="", dat_path=""):
        self._mmdb_path=mmdb_path
        self._dat_path=dat_path
        self._mmdb=None
        self._dat=None

    def load(self):
        if self._dat_path:
            try:
                self._dat=GeoIPDat(self._dat_path)
                self._dat.load()
                if self._dat._data:
                    return
            except Exception as e:
                logger.warning(f"geoip dat load error: {e}")
        if self._mmdb_path:
            try:
                import geoip2.database
                self._mmdb=geoip2.database.Reader(self._mmdb_path)
                logger.info(f"geoip mmdb loaded: {self._mmdb_path}")
            except FileNotFoundError:
                logger.warning(f"geoip mmdb not found: {self._mmdb_path}")
            except Exception as e:
                logger.warning(f"geoip mmdb load error: {e}")

    def country_code(self, ip):
        if self._dat:
            c=self._dat.country_code(ip)
            if c:
                return c
        if self._mmdb:
            try:
                return self._mmdb.country(ip).country.iso_code or ""
            except Exception:
                pass
        return ""

    def matches(self, ip, code):
        if self._dat and self._dat.matches(ip,code):
            return True
        if self._mmdb:
            try:
                return self._mmdb.country(ip).country.iso_code.upper()==code.upper()
            except Exception:
                pass
        return False
