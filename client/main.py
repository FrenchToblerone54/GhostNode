#!/usr/bin/env python3.13
import asyncio
import json
import logging
import os
import sys
import time
import urllib.parse
from config import VERSION

CONFIGS_PATH=os.path.expanduser("~/.config/ghostnode/clients.json")
logging.basicConfig(level=logging.INFO,format="%(levelname)s %(message)s")
logger=logging.getLogger(__name__)

def _fmt_bytes(n):
    if n<1024: return f"{n} B"
    if n<1048576: return f"{n/1024:.1f} KB"
    if n<1073741824: return f"{n/1048576:.1f} MB"
    return f"{n/1073741824:.1f} GB"

def load_configs():
    if not os.path.exists(CONFIGS_PATH):
        return {}
    with open(CONFIGS_PATH,"r") as f:
        return json.load(f)

def save_configs(configs):
    os.makedirs(os.path.dirname(CONFIGS_PATH),exist_ok=True)
    with open(CONFIGS_PATH,"w") as f:
        json.dump(configs,f,indent=2)

def parse_gn_link(link):
    link=link.strip()
    if not link.startswith("gn://"):
        raise ValueError("invalid gn:// link")
    rest=link[5:]
    if "@" not in rest:
        raise ValueError("missing @ in link")
    nanoid,hostpart=rest.split("@",1)
    if "?" in hostpart:
        hostport,query=hostpart.split("?",1)
    else:
        hostport,query=hostpart,""
    name=""
    if "#" in query:
        query,fragment=query.split("#",1)
        name=urllib.parse.unquote(fragment)
    if ":" in hostport:
        host,port_str=hostport.rsplit(":",1)
        port=int(port_str)
    else:
        host=hostport
        port=443
    params=dict(urllib.parse.parse_qsl(query))
    transport=params.get("transport","ws")
    path=params.get("path","/gn")
    security=params.get("security","none")
    sni=params.get("sni","")
    url_scheme="wss" if security=="tls" else "ws"
    if transport in ("h2","http2","hr","http-request","sse","http-request-sse","hrb","http-request-body"):
        url_scheme="https" if security=="tls" else "http"
    url=f"{url_scheme}://{host}:{port}"
    return {"nanoid":nanoid,"name":name or host,"url":url,"transport":transport,"path":path,"sni":sni,"allow_insecure":security=="none" and not sni,"host":host,"port":port,"fp":params.get("fp","")}

def cmd_import(args):
    if not args:
        print("usage: ghostnode-client import <gn://link>")
        sys.exit(1)
    link=" ".join(args)
    try:
        cfg=parse_gn_link(link)
    except ValueError as e:
        print(f"error: {e}")
        sys.exit(1)
    configs=load_configs()
    name=cfg["name"]
    configs[name]=cfg
    save_configs(configs)
    print(f"imported: {name}")

def cmd_list(args):
    configs=load_configs()
    if not configs:
        print("no configs saved")
        return
    for name,cfg in configs.items():
        print(f"  {name}  {cfg.get('url','')}  [{cfg.get('transport','ws')}]")

def cmd_run(args):
    config_name=None
    socks_host="127.0.0.1"
    socks_port=1080
    i=0
    while i<len(args):
        if args[i]=="--config" and i+1<len(args):
            config_name=args[i+1]
            i+=2
        elif args[i]=="--port" and i+1<len(args):
            socks_port=int(args[i+1])
            i+=2
        else:
            config_name=args[i]
            i+=1
    configs=load_configs()
    if not configs:
        print("no configs saved, use: ghostnode-client import <gn://link>")
        sys.exit(1)
    if config_name:
        cfg=configs.get(config_name)
        if not cfg:
            print(f"config not found: {config_name}")
            sys.exit(1)
    else:
        cfg=next(iter(configs.values()))
    try:
        asyncio.run(_run_client(cfg,socks_host,socks_port))
    except KeyboardInterrupt:
        pass

async def _run_client(cfg, socks_host, socks_port):
    from client.connector import connect_to_server
    from client.socks5 import start_socks5_server
    stats={"active":0,"total":0,"sent":0,"recv":0}
    async def connect_fn(addr, port, atyp):
        return await connect_to_server(cfg,addr,port,atyp)
    server=await start_socks5_server(socks_host,socks_port,connect_fn,stats)
    start_time=time.time()
    async def _ui():
        while True:
            elapsed=int(time.time()-start_time)
            h,r=divmod(elapsed,3600)
            m,s=divmod(r,60)
            print(f"\033[2J\033[H\033[?25lGhostNode Client {VERSION}\n\n  Server    {cfg.get('url','')}\n  Transport {cfg.get('transport','ws')}\n  Proxy     {socks_host}:{socks_port}\n\n  Active connections  {stats['active']}\n  Total connections   {stats['total']}\n  Bytes sent          {_fmt_bytes(stats['sent'])}\n  Bytes received      {_fmt_bytes(stats['recv'])}\n  Uptime              {h:02d}:{m:02d}:{s:02d}\n\nPress Ctrl+C to stop",flush=True)
            await asyncio.sleep(1)
    ui_task=asyncio.create_task(_ui()) if sys.stdout.isatty() else None
    if not ui_task:
        print(f"GhostNode Client {VERSION}\nSOCKS5 proxy on {socks_host}:{socks_port}\nServer: {cfg.get('url','')} [{cfg.get('transport','ws')}]")
    try:
        async with server:
            await server.serve_forever()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if ui_task:
            ui_task.cancel()
        server.close()
        await server.wait_closed()
        pending=[t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0.1)
        if sys.stdout.isatty():
            print("\033[?25h\n\nStopped.",flush=True)

def cmd_remove(args):
    if not args:
        print("usage: ghostnode-client remove <name>")
        sys.exit(1)
    name=" ".join(args)
    configs=load_configs()
    if name not in configs:
        print(f"config not found: {name}")
        sys.exit(1)
    del configs[name]
    save_configs(configs)
    print(f"removed: {name}")

def cmd_qr(args):
    name=args[0] if args else None
    configs=load_configs()
    if not configs:
        print("no configs")
        return
    cfg=configs.get(name) if name else next(iter(configs.values()))
    if not cfg:
        print(f"config not found: {name}")
        return
    nanoid=cfg["nanoid"]
    host=cfg["host"]
    port=cfg["port"]
    transport=cfg["transport"]
    path=cfg["path"]
    security="tls" if cfg.get("url","").startswith("wss://") or cfg.get("url","").startswith("https://") else "none"
    link=f"gn://{nanoid}@{host}:{port}?transport={transport}&path={urllib.parse.quote(path)}&security={security}#{urllib.parse.quote(cfg['name'])}"
    try:
        import qrcode
        qr=qrcode.QRCode()
        qr.add_data(link)
        qr.make()
        qr.print_ascii(invert=True)
    except ImportError:
        print(link)

def main():
    args=sys.argv[1:]
    if args and args[0]=="--version":
        print(f"ghostnode-client {VERSION}")
        return
    if not args or args[0] in ("-h","--help"):
        print(f"ghostnode-client {VERSION}\n\nCommands:\n  import <gn://link>       import server config from a gn:// link\n  list                     list saved configs\n  remove <name>            remove a saved config\n  run [name] [--port 1080] start SOCKS5 proxy\n  qr [name]                show QR code for a config\n\nOptions:\n  -h, --help               show this help\n  --version                show version")
        return
    cmd=args[0]
    rest=args[1:]
    if cmd=="import":
        cmd_import(rest)
    elif cmd=="list":
        cmd_list(rest)
    elif cmd=="remove":
        cmd_remove(rest)
    elif cmd=="run":
        cmd_run(rest)
    elif cmd=="qr":
        cmd_qr(rest)
    else:
        print(f"unknown command: {cmd}")
        sys.exit(1)

if __name__=="__main__":
    main()
