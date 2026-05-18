#!/usr/bin/env python3.13
import logging
import re

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

def _parse_domain_msg(data):
    domain_type=0
    value=""
    pos=0
    while pos<len(data):
        fn,wt,val,pos=_read_field(data,pos)
        if fn is None:
            break
        if fn==1 and wt==0:
            domain_type=val
        elif fn==2 and wt==2:
            value=val.decode("utf-8","ignore")
    return domain_type,value

def _parse_geosite_entry(data):
    country_code=""
    domains=[]
    pos=0
    while pos<len(data):
        fn,wt,val,pos=_read_field(data,pos)
        if fn is None:
            break
        if fn==1 and wt==2:
            country_code=val.decode("utf-8","ignore").upper()
        elif fn==2 and wt==2:
            dt,dv=_parse_domain_msg(val)
            if dv:
                domains.append((dt,dv))
    return country_code,domains

class GeoSite:
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
                    code,domains=_parse_geosite_entry(val)
                    if code:
                        self._data[code]=domains
            logger.info(f"geosite loaded {len(self._data)} countries from {self._path}")
        except FileNotFoundError:
            logger.warning(f"geosite dat not found: {self._path}")
        except Exception as e:
            logger.warning(f"geosite load error: {e}")

    def matches(self, domain, code):
        entries=self._data.get(code.upper(),[])
        domain=domain.lower().lstrip(".")
        for dtype,dval in entries:
            dval=dval.lower()
            if dtype==0:
                if domain==dval or domain.endswith("."+dval):
                    return True
            elif dtype==1:
                try:
                    if re.search(dval,domain):
                        return True
                except re.error:
                    pass
            elif dtype==2:
                if domain==dval or domain.endswith("."+dval):
                    return True
            elif dtype==3:
                if domain==dval:
                    return True
        return False
