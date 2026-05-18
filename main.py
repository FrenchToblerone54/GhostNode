#!/usr/bin/env python3.13
import argparse
import asyncio
import json
import logging
import os
import signal
import sys

def setup_logging(level_str, log_file):
    level=getattr(logging,level_str.upper(),logging.INFO)
    handlers=[logging.StreamHandler()]
    if log_file:
        try:
            os.makedirs(os.path.dirname(log_file),exist_ok=True)
            handlers.append(logging.FileHandler(log_file))
        except Exception:
            pass
    logging.basicConfig(level=level,format="%(asctime)s %(levelname)s %(name)s %(message)s",handlers=handlers)

async def main():
    parser=argparse.ArgumentParser(description="GhostNode Proxy Server")
    parser.add_argument("-c","--config",default=None,help="config file path")
    parser.add_argument("--version",action="store_true")
    parser.add_argument("--generate-token",action="store_true",help="generate a random 20-char nanoid panel token")
    args=parser.parse_args()
    from config import GhostNodeConfig,VERSION,write_default_config,DEFAULT_CONFIG_PATH
    if args.version:
        print(VERSION)
        return
    if args.generate_token:
        from core.protocol import generate_nanoid
        print(generate_nanoid())
        return
    cfg_path=args.config or DEFAULT_CONFIG_PATH
    if not os.path.exists(cfg_path) and cfg_path==DEFAULT_CONFIG_PATH:
        write_default_config(cfg_path)
    cfg=GhostNodeConfig(cfg_path)
    setup_logging(cfg.log_level,cfg.log_file)
    logger=logging.getLogger(__name__)
    logger.info(f"GhostNode {VERSION} starting")
    from db import Database
    from updater import Updater
    db=Database(cfg.db_path)
    await db.init()
    logger.info(f"database: {cfg.db_path}")
    from core.crypto import load_or_create_server_keys
    await load_or_create_server_keys(db)
    logger.info("server crypto keys initialized")
    geoip=None
    geosite=None
    try:
        from geo.geoip import GeoIP
        geoip=GeoIP(mmdb_path=cfg.geoip_path,dat_path=cfg.geoip_dat_path)
        geoip.load()
    except Exception as e:
        logger.warning(f"geoip init failed: {e}")
    try:
        from geo.geosite import GeoSite
        geosite=GeoSite(cfg.geosite_path)
        geosite.load()
    except Exception as e:
        logger.warning(f"geosite init failed: {e}")
    from core.traffic import TrafficController
    traffic_ctrl=TrafficController(db)
    await traffic_ctrl.start()
    outbound_rows=await db.get_outbounds()
    from core.outbound import build_outbound
    outbounds={row["tag"]:build_outbound({"type":row["type"],"config":row["config"]}) for row in outbound_rows}
    for _ob in outbounds.values():
        if getattr(_ob,"_dialer_proxy_tag",""):
            _ob._dialer=outbounds.get(_ob._dialer_proxy_tag)
    rule_rows=await db.get_routing_rules()
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
    loop=asyncio.get_event_loop()
    from core.router import Router
    router=Router(rules,outbounds,geoip,geosite,db=db,loop=loop)
    from core.xray import XrayManager
    xray_mgr=XrayManager()
    xray_mgr.set_db(db)
    traffic_ctrl.set_xray(xray_mgr)
    if xray_mgr.is_installed():
        await xray_mgr.start()
    from inbound.manager import InboundManager
    inbound_mgr=InboundManager(db,router,traffic_ctrl)
    await inbound_mgr.start_all()
    if cfg.panel_enabled:
        from panel.api import start_panel
        start_panel(cfg,db,inbound_mgr,traffic_ctrl,loop,router,xray_mgr=xray_mgr)
        logger.info(f"panel: http://{cfg.panel_host}:{cfg.panel_port}/{cfg.panel_path}/")
    stop_event=asyncio.Event()
    def _handle_signal(sig,frame):
        logger.info(f"received {sig}, shutting down")
        loop.call_soon_threadsafe(stop_event.set)
    signal.signal(signal.SIGTERM,_handle_signal)
    signal.signal(signal.SIGINT,_handle_signal)
    if cfg.auto_update:
        updater=Updater(check_interval=cfg.update_check_interval,check_on_startup=cfg.update_check_on_startup,http_proxy=cfg.update_proxy,https_proxy=cfg.update_proxy)
        asyncio.create_task(updater.update_loop(stop_event))
    logger.info("GhostNode running")
    await stop_event.wait()
    logger.info("stopping inbounds")
    await inbound_mgr.stop_all()
    await db.close()
    logger.info("GhostNode stopped")

if __name__=="__main__":
    asyncio.run(main())
