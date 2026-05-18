#!/usr/bin/env python3.13
import asyncio
import json
import re
import ipaddress
import logging
import traceback
from core.outbound import build_outbound

logger=logging.getLogger(__name__)

class Router:
    def __init__(self, rules, outbounds, geoip=None, geosite=None, db=None, loop=None):
        self._rules=rules
        self._outbounds=outbounds
        self._geoip=geoip
        self._geosite=geosite
        self._db=db
        self._loop=loop

    def reload(self):
        if not self._db or not self._loop:
            return
        async def _do():
            rule_rows=await self._db.get_routing_rules()
            rules=[]
            for r in rule_rows:
                if not r.get("enabled",1):
                    continue
                rule={"outbound_tag":r["outbound_tag"]}
                for f in ("domain","ip"):
                    if r[f]:
                        try:
                            rule[f]=json.loads(r[f])
                        except Exception:
                            rule[f]=r[f].split(",")
                for f in ("port","protocol","inbound_tag"):
                    if r[f]:
                        rule[f]=r[f]
                rules.append(rule)
            outbound_rows=await self._db.get_outbounds()
            outbounds={row["tag"]:build_outbound({"type":row["type"],"config":row["config"]}) for row in outbound_rows}
            for ob in outbounds.values():
                if getattr(ob,"_dialer_proxy_tag",""):
                    ob._dialer=outbounds.get(ob._dialer_proxy_tag)
            self._rules=rules
            self._outbounds=outbounds
        future=asyncio.run_coroutine_threadsafe(_do(),self._loop)
        try:
            future.result(timeout=5)
        except Exception as e:
            logger.warning(f"router reload error: {e}\n{traceback.format_exc()}")

    def _match_domain(self, matchers, domain):
        if not domain:
            return False
        for m in matchers:
            if m.startswith("geosite:"):
                code=m[8:].upper()
                if self._geosite and self._geosite.matches(domain,code):
                    return True
            elif m.startswith("full:"):
                if domain==m[5:]:
                    return True
            elif m.startswith("regexp:"):
                if re.search(m[7:],domain):
                    return True
            elif m.startswith("domain:"):
                suffix=m[7:]
                if domain==suffix or domain.endswith("."+suffix):
                    return True
            else:
                if domain==m or domain.endswith("."+m):
                    return True
        return False

    def _match_ip(self, matchers, ip):
        if not ip:
            return False
        try:
            addr=ipaddress.ip_address(ip)
        except ValueError:
            return False
        for m in matchers:
            if m.startswith("geoip:"):
                code=m[6:].upper()
                if self._geoip and self._geoip.matches(ip,code):
                    return True
            else:
                try:
                    if addr in ipaddress.ip_network(m,strict=False):
                        return True
                except ValueError:
                    pass
        return False

    def _match_port(self, port_spec, port):
        if not port_spec or not port:
            return False
        for part in port_spec.split(","):
            part=part.strip()
            if "-" in part:
                lo,hi=part.split("-",1)
                if int(lo)<=port<=int(hi):
                    return True
            elif int(part)==port:
                return True
        return False

    def match(self, conn_info):
        domain=conn_info.get("domain","")
        ip=conn_info.get("ip","")
        port=conn_info.get("port")
        protocol=conn_info.get("protocol","tcp")
        inbound_tag=conn_info.get("inbound_tag","")
        user=conn_info.get("user","")
        for rule in self._rules:
            matched=True
            if rule.get("domain") and not self._match_domain(rule["domain"],domain):
                matched=False
            if matched and rule.get("ip") and not self._match_ip(rule["ip"],ip):
                matched=False
            if matched and rule.get("port") and not self._match_port(rule["port"],port):
                matched=False
            if matched and rule.get("protocol") and rule["protocol"]!=protocol:
                matched=False
            if matched and rule.get("inbound_tag") and rule["inbound_tag"]!=inbound_tag:
                matched=False
            if matched and rule.get("user") and rule["user"]!=user:
                matched=False
            if matched:
                return rule.get("outbound_tag","direct")
        return "direct"
