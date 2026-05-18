#!/usr/bin/env python3.13
import asyncio
import collections
import io
import json
import logging
import os
import queue
import socket
import sqlite3
import sys
import threading
import time
import tomllib
import toml
import urllib.parse
import uuid
import psutil
import qrcode
import qrcode.image.svg
from flask import Flask,Response,jsonify,request,send_from_directory
from waitress import serve as waitress_serve
from config import VERSION
from core.crypto import get_server_fingerprint
from core.protocol import generate_nanoid
from core.xray import XrayManager
from updater import Updater,GITHUB_REPO

logger=logging.getLogger(__name__)

_log_buffer=collections.deque(maxlen=2000)
_log_subscribers=[]
_log_subscribers_lock=threading.Lock()

class _PanelLogHandler(logging.Handler):
    def emit(self, record):
        line=self.format(record)
        _log_buffer.append(line)
        with _log_subscribers_lock:
            for q in _log_subscribers:
                try:
                    q.put_nowait(line)
                except Exception:
                    pass

_panel_handler=_PanelLogHandler()
_panel_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

app=Flask(__name__)

@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(500)
def _http_error(e):
    return jsonify({"error":e.description}),e.code

@app.errorhandler(Exception)
def _handle_exc(e):
    if isinstance(e,sqlite3.IntegrityError):
        return jsonify({"error":str(e)}),409
    logger.error(f"panel error: {e}")
    return jsonify({"error":"internal server error"}),500

_panel_cfg=None
_db=None
_inbound_mgr=None
_traffic_ctrl=None
_loop=None
_router=None
_xray_mgr=None
_start_time=time.time()
_routes=[]

def _frontend_dir():
    if getattr(sys,"_MEIPASS",None):
        return os.path.join(sys._MEIPASS,"panel","frontend")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"panel","frontend")

def _load_html():
    with open(os.path.join(_frontend_dir(),"index.html"),"r") as f:
        return f.read()

def _prefix():
    return f"/{_panel_cfg.panel_path}" if _panel_cfg and _panel_cfg.panel_path else ""

@app.before_request
def check_prefix():
    if _panel_cfg and _panel_cfg.panel_path and not request.path.startswith(f"/{_panel_cfg.panel_path}"):
        return Response("",status=404)

def panel_route(path="",methods=["GET"]):
    def decorator(func):
        _routes.append((path,methods,func))
        return func
    return decorator

def _run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro,_loop).result(timeout=10)

def _get_system_info():
    try:
        cpu=psutil.cpu_percent(interval=0.2)
        ram=psutil.virtual_memory()
        swap=psutil.swap_memory()
        disk=psutil.disk_usage("/")
        net1=psutil.net_io_counters()
        time.sleep(0.5)
        net2=psutil.net_io_counters()
        sent=(net2.bytes_sent-net1.bytes_sent)*2
        recv=(net2.bytes_recv-net1.bytes_recv)*2
        load=os.getloadavg() if hasattr(os,"getloadavg") else (0,0,0)
        uptime=int(time.time()-_start_time)
        d,r=divmod(uptime,86400)
        h,r=divmod(r,3600)
        m,s=divmod(r,60)
        uptime_str=f"{d}d {h}h" if d else (f"{h}h {m}m" if h else f"{m}m {s}s")
        return {"cpu_percent":round(cpu,1),"cpu_count":psutil.cpu_count(),"ram_used":ram.used,"ram_total":ram.total,"ram_percent":round(ram.percent,1),"swap_used":swap.used,"swap_total":swap.total,"swap_percent":round(swap.percent,1),"disk_used":disk.used,"disk_total":disk.total,"disk_percent":round(disk.percent,1),"net_sent":sent,"net_recv":recv,"load_1":round(load[0],2),"load_5":round(load[1],2),"load_15":round(load[2],2),"uptime":uptime_str}
    except Exception as e:
        return {"error":str(e)}

def _make_gn_link(client, inbound):
    nanoid=client["nanoid"]
    name=client["name"]
    ext_host=inbound.get("ext_host","")
    ext_port=inbound.get("ext_port",0)
    ext_tls=inbound.get("ext_tls",0)
    host=ext_host if ext_host else ((_panel_cfg.hostname if _panel_cfg and _panel_cfg.hostname else "") or _get_public_ip())
    port=ext_port if ext_port else inbound["port"]
    transport=inbound.get("transport","websocket")
    path=inbound.get("path","/gn")
    security="tls" if (ext_tls or inbound.get("ssl_cert")) else "none"
    transport_map={"websocket":"ws","http2":"h2","grpc":"grpc","http-request":"hr","http-request-sse":"sse","http-request-body":"hrb"}
    t=transport_map.get(transport,"ws")
    inbound_host=inbound.get("host","")
    p={"transport":t,"path":path,"security":security}
    if inbound_host:
        p["host"]=inbound_host
    try:
        p["fp"]=get_server_fingerprint()
    except Exception:
        pass
    params=urllib.parse.urlencode(p)
    return f"gn://{nanoid}@{host}:{port}?{params}#{urllib.parse.quote(name)}"

def _get_public_ip():
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8",80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"

def _make_qr_svg(data):
    import re
    factory=qrcode.image.svg.SvgPathImage
    img=qrcode.make(data,image_factory=factory,box_size=10,border=4)
    buf=io.BytesIO()
    img.save(buf)
    svg=buf.getvalue().decode("utf-8")
    svg=re.sub(r'^<\?xml[^?]*\?>\s*','',svg)
    svg=re.sub(r'\s(?:width|height)="[^"]*"','',svg,count=2)
    svg=svg.replace('<path ','<rect width="100%" height="100%" fill="#fff"/><path ',1)
    return svg

@panel_route("/")
def index():
    pfx=_prefix()
    return _load_html().replace("{{prefix}}",pfx)

@panel_route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(os.path.join(_frontend_dir(),"static"),filename)

@panel_route("/api/stats")
def api_stats():
    info=_get_system_info()
    info["version"]=VERSION
    return jsonify(info)

@panel_route("/api/inbounds",methods=["GET","POST"])
def api_inbounds():
    if request.method=="POST":
        d=request.json or {}
        id=_run_async(_db.create_inbound(d["tag"],d["port"],d.get("transport","websocket"),d.get("path","/gn"),d.get("ssl_cert",""),d.get("ssl_key",""),d.get("listen_ip","0.0.0.0")))
        if _inbound_mgr:
            inb=_run_async(_db.get_inbound(id))
            asyncio.run_coroutine_threadsafe(_inbound_mgr._start_inbound(inb),_loop)
        return jsonify({"id":id}),201
    return jsonify(_run_async(_db.get_inbounds()))

@panel_route("/api/inbounds/<int:id>",methods=["PUT","DELETE"])
def api_inbound(id):
    if request.method=="DELETE":
        inb=_run_async(_db.get_inbound(id))
        if inb and _inbound_mgr:
            asyncio.run_coroutine_threadsafe(_inbound_mgr._stop_inbound(inb["tag"]),_loop)
        _run_async(_db.delete_inbound(id))
        return jsonify({"ok":True})
    d=request.json or {}
    allowed={"tag","port","transport","path","ssl_cert","ssl_key","enabled","ext_host","ext_port","ext_tls","host","sni","listen_ip"}
    kwargs={k:v for k,v in d.items() if k in allowed}
    _run_async(_db.update_inbound(id,**kwargs))
    if _inbound_mgr:
        inb=_run_async(_db.get_inbound(id))
        if inb:
            asyncio.run_coroutine_threadsafe(_inbound_mgr.restart_inbound(inb["tag"]),_loop)
    return jsonify({"ok":True})

@panel_route("/api/inbounds/bulk",methods=["POST"])
def api_inbounds_bulk():
    d=request.json or {}
    action=d.get("action")
    ids=d.get("ids",[])
    for id in ids:
        if action=="enable":
            _run_async(_db.update_inbound(id,enabled=1))
        elif action=="disable":
            _run_async(_db.update_inbound(id,enabled=0))
        elif action=="delete":
            inb=_run_async(_db.get_inbound(id))
            if inb and _inbound_mgr:
                asyncio.run_coroutine_threadsafe(_inbound_mgr._stop_inbound(inb["tag"]),_loop)
            _run_async(_db.delete_inbound(id))
    return jsonify({"ok":True})

@panel_route("/api/clients",methods=["GET","POST"])
def api_clients():
    if request.method=="POST":
        d=request.json or {}
        nanoid=generate_nanoid()
        limit_gb=float(d.get("traffic_limit_gb",0))
        limit_bytes=int(limit_gb*1024*1024*1024) if limit_gb>0 else 0
        id=_run_async(_db.create_client(nanoid,d["name"],d["inbound_tag"],limit_bytes,d.get("expire_date","")))
        client=_run_async(_db.get_client(id))
        return jsonify(client),201
    return jsonify(_run_async(_db.get_clients()))

@panel_route("/api/clients/<int:id>",methods=["PUT","DELETE"])
def api_client(id):
    if request.method=="DELETE":
        _run_async(_db.delete_client(id))
        return jsonify({"ok":True})
    d=request.json or {}
    if "traffic_limit_gb" in d:
        d["traffic_limit"]=int(float(d.pop("traffic_limit_gb"))*1024*1024*1024)
    allowed={"name","inbound_tag","traffic_limit","expire_date","enabled"}
    kwargs={k:v for k,v in d.items() if k in allowed}
    _run_async(_db.update_client(id,**kwargs))
    return jsonify({"ok":True})

@panel_route("/api/clients/bulk",methods=["POST"])
def api_clients_bulk():
    d=request.json or {}
    action=d.get("action")
    ids=d.get("ids",[])
    for id in ids:
        if action=="enable":
            _run_async(_db.update_client(id,enabled=1))
        elif action=="disable":
            _run_async(_db.update_client(id,enabled=0))
        elif action=="delete":
            _run_async(_db.delete_client(id))
    return jsonify({"ok":True})

@panel_route("/api/clients/<int:id>/reset",methods=["POST"])
def api_client_reset(id):
    _run_async(_db.reset_client_traffic(id))
    return jsonify({"ok":True})

@panel_route("/api/clients/<int:id>/toggle",methods=["POST"])
def api_client_toggle(id):
    client=_run_async(_db.get_client(id))
    if client:
        _run_async(_db.update_client(id,enabled=0 if client["enabled"] else 1))
    return jsonify({"ok":True})

@panel_route("/api/clients/<int:id>/config-link",methods=["POST"])
def api_client_config_link(id):
    client=_run_async(_db.get_client(id))
    if not client:
        return jsonify({"error":"not found"}),404
    inbounds=_run_async(_db.get_inbounds())
    inbound=[i for i in inbounds if i["tag"]==client["inbound_tag"]]
    if not inbound:
        return jsonify({"error":"inbound not found"}),404
    link=_make_gn_link(client,inbound[0])
    qr_svg=_make_qr_svg(link)
    return jsonify({"link":link,"qr_svg":qr_svg})

def _reload_router():
    if _router:
        _router.reload()

@panel_route("/api/outbounds",methods=["GET","POST"])
def api_outbounds():
    if request.method=="POST":
        d=request.json or {}
        cfg=d.get("config",{})
        if isinstance(cfg,dict):
            cfg=json.dumps(cfg)
        id=_run_async(_db.create_outbound(d["tag"],d.get("type","direct"),cfg))
        _reload_router()
        return jsonify({"id":id}),201
    return jsonify(_run_async(_db.get_outbounds()))

@panel_route("/api/outbounds/<int:id>",methods=["PUT","DELETE"])
def api_outbound(id):
    if request.method=="DELETE":
        _run_async(_db.delete_outbound(id))
        _reload_router()
        return jsonify({"ok":True})
    d=request.json or {}
    if "config" in d and isinstance(d["config"],dict):
        d["config"]=json.dumps(d["config"])
    allowed={"tag","type","config"}
    kwargs={k:v for k,v in d.items() if k in allowed}
    _run_async(_db.update_outbound(id,**kwargs))
    _reload_router()
    return jsonify({"ok":True})

@panel_route("/api/outbounds/reorder",methods=["POST"])
def api_outbounds_reorder():
    _run_async(_db.reorder_outbounds(request.json or []))
    _reload_router()
    return jsonify({"ok":True})

@panel_route("/api/routing",methods=["GET","POST"])
def api_routing():
    if request.method=="POST":
        d=request.json or {}
        domain=d.get("domain","")
        ip=d.get("ip","")
        if isinstance(domain,list):
            domain=json.dumps(domain)
        if isinstance(ip,list):
            ip=json.dumps(ip)
        id=_run_async(_db.create_routing_rule(d["outbound_tag"],domain,ip,d.get("port",""),d.get("protocol",""),d.get("inbound_tag",""),d.get("ord",0)))
        _reload_router()
        return jsonify({"id":id}),201
    rules=_run_async(_db.get_routing_rules())
    for r in rules:
        for f in ("domain","ip"):
            if r[f]:
                try:
                    r[f]=json.loads(r[f])
                except Exception:
                    r[f]=r[f].split(",") if r[f] else []
            else:
                r[f]=[]
    return jsonify(rules)

@panel_route("/api/routing/<int:id>",methods=["PUT","DELETE"])
def api_rule(id):
    if request.method=="DELETE":
        _run_async(_db.delete_routing_rule(id))
        _reload_router()
        return jsonify({"ok":True})
    d=request.json or {}
    for f in ("domain","ip"):
        if f in d and isinstance(d[f],list):
            d[f]=json.dumps(d[f])
    allowed={"domain","ip","port","protocol","inbound_tag","outbound_tag","ord","enabled"}
    kwargs={k:v for k,v in d.items() if k in allowed}
    _run_async(_db.update_routing_rule(id,**kwargs))
    _reload_router()
    return jsonify({"ok":True})

@panel_route("/api/routing/<int:id>/toggle",methods=["POST"])
def api_rule_toggle(id):
    rule=_run_async(_db.get_routing_rule(id))
    if not rule:
        return jsonify({"error":"not found"}),404
    _run_async(_db.update_routing_rule(id,enabled=0 if rule["enabled"] else 1))
    _reload_router()
    return jsonify({"ok":True})

@panel_route("/api/routing/reorder",methods=["POST"])
def api_routing_reorder():
    _run_async(_db.reorder_rules(request.json or []))
    _reload_router()
    return jsonify({"ok":True})

@panel_route("/api/logs")
def api_logs():
    lines=int(request.args.get("lines",200))
    log_file=_panel_cfg.log_file if _panel_cfg else ""
    if log_file and os.path.exists(log_file):
        try:
            with open(log_file,"r") as f:
                all_lines=f.readlines()
            return jsonify({"lines":[l.rstrip() for l in all_lines[-lines:]]})
        except Exception:
            pass
    return jsonify({"lines":list(_log_buffer)[-lines:]})

@panel_route("/api/logs/stream")
def api_logs_stream():
    q=queue.Queue()
    with _log_subscribers_lock:
        _log_subscribers.append(q)
    def generate():
        try:
            while True:
                try:
                    line=q.get(timeout=15)
                    yield f"data: {line}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            with _log_subscribers_lock:
                try:
                    _log_subscribers.remove(q)
                except ValueError:
                    pass
    return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@panel_route("/api/version")
def api_version():
    return jsonify({"version":VERSION})

@panel_route("/api/config")
def api_config_get():
    cfg=_panel_cfg
    return jsonify({
        "panel_host":cfg.panel_host,"panel_port":cfg.panel_port,
        "panel_path":cfg.panel_path,"panel_threads":cfg.panel_threads,
        "log_level":cfg.log_level,"log_file":cfg.log_file,
        "hostname":cfg.hostname,"auto_update":cfg.auto_update,
        "update_check_interval":cfg.update_check_interval,
        "update_check_on_startup":cfg.update_check_on_startup,
        "update_proxy":cfg.update_proxy,
    })

@panel_route("/api/config",methods=["POST"])
def api_config_save():
    d=request.json or {}
    path=_panel_cfg.config_path
    raw={}
    if os.path.exists(path):
        with open(path,"rb") as f:
            raw=tomllib.load(f)
    raw.setdefault("panel",{})
    raw.setdefault("logging",{})
    raw.setdefault("server",{})
    if "panel_host" in d: raw["panel"]["host"]=d["panel_host"]
    if "panel_port" in d: raw["panel"]["port"]=int(d["panel_port"])
    if "panel_path" in d: raw["panel"]["path"]=d["panel_path"]
    if "panel_threads" in d: raw["panel"]["threads"]=int(d["panel_threads"])
    if "log_level" in d: raw["logging"]["level"]=d["log_level"]
    if "log_file" in d: raw["logging"]["file"]=d["log_file"]
    if "hostname" in d: raw["server"]["hostname"]=d["hostname"]
    if "auto_update" in d: raw["server"]["auto_update"]=bool(d["auto_update"])
    if "update_check_interval" in d: raw["server"]["update_check_interval"]=int(d["update_check_interval"])
    if "update_check_on_startup" in d: raw["server"]["update_check_on_startup"]=bool(d["update_check_on_startup"])
    if "update_proxy" in d: raw["server"]["update_proxy"]=d["update_proxy"]
    with open(path,"w") as f:
        toml.dump(raw,f)
    return jsonify({"ok":True})

@panel_route("/api/restart",methods=["POST"])
def api_restart():
    def _do():
        time.sleep(0.5)
        os.execv(sys.executable,sys.argv)
    threading.Thread(target=_do,daemon=True).start()
    return jsonify({"ok":True})

@panel_route("/api/update/check")
def api_update_check():
    proxy=_panel_cfg.update_proxy if _panel_cfg else ""
    u=Updater(http_proxy=proxy,https_proxy=proxy)
    new_version=asyncio.run_coroutine_threadsafe(u.check_for_update(),_loop).result(timeout=15)
    return jsonify({"update_available":bool(new_version),"latest":new_version,"current":u.current_version,"repo":GITHUB_REPO})

@panel_route("/api/update/install",methods=["POST"])
def api_update_install():
    proxy=_panel_cfg.update_proxy if _panel_cfg else ""
    u=Updater(http_proxy=proxy,https_proxy=proxy)
    def _do():
        asyncio.run_coroutine_threadsafe(u.download_update("latest"),_loop).result(timeout=300)
    threading.Thread(target=_do,daemon=True).start()
    return jsonify({"ok":True})

@panel_route("/api/xray/status")
def api_xray_status():
    installed=_xray_mgr.is_installed() if _xray_mgr else False
    running=_xray_mgr.is_running() if _xray_mgr else False
    version=_run_async(_xray_mgr.get_version()) if _xray_mgr and installed else None
    socks_port=_xray_mgr._socks_port if _xray_mgr else 10808
    return jsonify({"installed":installed,"running":running,"version":version,"socks_port":socks_port})

@panel_route("/api/xray/install",methods=["POST"])
def api_xray_install():
    if not _xray_mgr:
        return jsonify({"error":"xray manager not available"}),500
    if getattr(_xray_mgr,"_installing",False):
        return jsonify({"error":"install already in progress"}),409
    d=request.json or {}
    proxy=d.get("proxy","") or (_panel_cfg.update_proxy if _panel_cfg else "")
    def _do():
        try:
            _xray_mgr.install(http_proxy=proxy)
        except Exception as e:
            logger.error(f"Xray install failed: {e}")
    threading.Thread(target=_do,daemon=True).start()
    return jsonify({"ok":True})

@panel_route("/api/xray/install-status",methods=["GET"])
def api_xray_install_status():
    if not _xray_mgr:
        return jsonify({"error":"xray manager not available"}),500
    return jsonify({"installing":getattr(_xray_mgr,"_installing",False),"progress":getattr(_xray_mgr,"_install_progress",{}),"error":getattr(_xray_mgr,"_install_error",None)})

@panel_route("/api/xray/start",methods=["POST"])
def api_xray_start():
    if _xray_mgr:
        asyncio.run_coroutine_threadsafe(_xray_mgr.start(),_loop)
    return jsonify({"ok":True})

@panel_route("/api/xray/stop",methods=["POST"])
def api_xray_stop():
    if _xray_mgr:
        asyncio.run_coroutine_threadsafe(_xray_mgr.stop(),_loop)
    return jsonify({"ok":True})

@panel_route("/api/xray/restart",methods=["POST"])
def api_xray_restart():
    if _xray_mgr:
        asyncio.run_coroutine_threadsafe(_xray_mgr.restart(),_loop)
    return jsonify({"ok":True})

@panel_route("/api/xray/inbounds",methods=["GET","POST"])
def api_xray_inbounds():
    if request.method=="POST":
        d=request.json or {}
        settings=d.get("settings",{})
        stream=d.get("stream_settings",{})
        if isinstance(settings,dict): settings=json.dumps(settings)
        if isinstance(stream,dict): stream=json.dumps(stream)
        id=_run_async(_db.create_xray_inbound(d["tag"],d["port"],d.get("protocol","vless"),settings,stream))
        if _xray_mgr and _xray_mgr.is_running():
            asyncio.run_coroutine_threadsafe(_xray_mgr.restart(),_loop)
        return jsonify({"id":id}),201
    return jsonify(_run_async(_db.get_xray_inbounds()))

@panel_route("/api/xray/inbounds/<int:id>",methods=["PUT","DELETE"])
def api_xray_inbound(id):
    if request.method=="DELETE":
        _run_async(_db.delete_xray_inbound(id))
        if _xray_mgr and _xray_mgr.is_running():
            asyncio.run_coroutine_threadsafe(_xray_mgr.restart(),_loop)
        return jsonify({"ok":True})
    d=request.json or {}
    for f in ("settings","stream_settings"):
        if f in d and isinstance(d[f],dict): d[f]=json.dumps(d[f])
    allowed={"tag","port","protocol","settings","stream_settings","enabled"}
    kwargs={k:v for k,v in d.items() if k in allowed}
    _run_async(_db.update_xray_inbound(id,**kwargs))
    if _xray_mgr and _xray_mgr.is_running():
        asyncio.run_coroutine_threadsafe(_xray_mgr.restart(),_loop)
    return jsonify({"ok":True})

@panel_route("/api/xray/outbounds",methods=["GET","POST"])
def api_xray_outbounds():
    if request.method=="POST":
        d=request.json or {}
        settings=d.get("settings",{})
        stream=d.get("stream_settings",{})
        if isinstance(settings,dict): settings=json.dumps(settings)
        if isinstance(stream,dict): stream=json.dumps(stream)
        id=_run_async(_db.create_xray_outbound(d["tag"],d.get("protocol","vmess"),settings,stream))
        if _xray_mgr and _xray_mgr.is_running():
            asyncio.run_coroutine_threadsafe(_xray_mgr.restart(),_loop)
        return jsonify({"id":id}),201
    return jsonify(_run_async(_db.get_xray_outbounds()))

@panel_route("/api/xray/outbounds/<int:id>",methods=["PUT","DELETE"])
def api_xray_outbound(id):
    if request.method=="DELETE":
        _run_async(_db.delete_xray_outbound(id))
        if _xray_mgr and _xray_mgr.is_running():
            asyncio.run_coroutine_threadsafe(_xray_mgr.restart(),_loop)
        return jsonify({"ok":True})
    d=request.json or {}
    for f in ("settings","stream_settings"):
        if f in d and isinstance(d[f],dict): d[f]=json.dumps(d[f])
    allowed={"tag","protocol","settings","stream_settings","ord"}
    kwargs={k:v for k,v in d.items() if k in allowed}
    _run_async(_db.update_xray_outbound(id,**kwargs))
    if _xray_mgr and _xray_mgr.is_running():
        asyncio.run_coroutine_threadsafe(_xray_mgr.restart(),_loop)
    return jsonify({"ok":True})

@panel_route("/api/xray/outbounds/<int:id>/test",methods=["POST"])
def api_xray_outbound_test(id):
    if not _xray_mgr or not _xray_mgr.is_installed():
        return jsonify({"error":"xray not installed"}),400
    ob=_run_async(_db.get_xray_outbound(id))
    if not ob:
        return jsonify({"error":"not found"}),404
    ob["settings"]=json.loads(ob["settings"]) if isinstance(ob["settings"],str) else ob["settings"]
    ob["stream_settings"]=json.loads(ob["stream_settings"]) if isinstance(ob["stream_settings"],str) else ob["stream_settings"]
    latency=_run_async(_xray_mgr.test_outbound(ob))
    return jsonify({"latency_ms":latency})

@panel_route("/api/xray/clients",methods=["GET","POST"])
def api_xray_clients():
    if request.method=="POST":
        d=request.json or {}
        uid=str(uuid.uuid4())
        limit_gb=float(d.get("traffic_limit_gb",0))
        limit_bytes=int(limit_gb*1024*1024*1024) if limit_gb>0 else 0
        id=_run_async(_db.create_xray_client(uid,d["name"],d["inbound_tag"],limit_bytes,d.get("expire_date","")))
        client=_run_async(_db.get_xray_client(id))
        if _xray_mgr and _xray_mgr.is_running():
            asyncio.run_coroutine_threadsafe(_xray_mgr.restart(),_loop)
        return jsonify(client),201
    return jsonify(_run_async(_db.get_xray_clients()))

@panel_route("/api/xray/clients/<int:id>",methods=["PUT","DELETE"])
def api_xray_client(id):
    if request.method=="DELETE":
        _run_async(_db.delete_xray_client(id))
        if _xray_mgr and _xray_mgr.is_running():
            asyncio.run_coroutine_threadsafe(_xray_mgr.restart(),_loop)
        return jsonify({"ok":True})
    d=request.json or {}
    if "traffic_limit_gb" in d:
        d["traffic_limit"]=int(float(d.pop("traffic_limit_gb"))*1024*1024*1024)
    allowed={"name","inbound_tag","traffic_limit","expire_date","enabled"}
    kwargs={k:v for k,v in d.items() if k in allowed}
    _run_async(_db.update_xray_client(id,**kwargs))
    if _xray_mgr and _xray_mgr.is_running():
        asyncio.run_coroutine_threadsafe(_xray_mgr.restart(),_loop)
    return jsonify({"ok":True})

@panel_route("/api/xray/clients/<int:id>/toggle",methods=["POST"])
def api_xray_client_toggle(id):
    client=_run_async(_db.get_xray_client(id))
    if client:
        _run_async(_db.update_xray_client(id,enabled=0 if client["enabled"] else 1))
        if _xray_mgr and _xray_mgr.is_running():
            asyncio.run_coroutine_threadsafe(_xray_mgr.restart(),_loop)
    return jsonify({"ok":True})

@panel_route("/api/xray/raw-config",methods=["GET","POST"])
def api_xray_raw_config():
    if not _xray_mgr:
        return jsonify({"error":"xray manager not available"}),500
    if request.method=="POST":
        cfg=request.json or {}
        os.makedirs(os.path.dirname(_xray_mgr._config_path),exist_ok=True)
        with open(_xray_mgr._config_path,"w") as f:
            json.dump(cfg,f,indent=2)
        if _xray_mgr.is_running():
            asyncio.run_coroutine_threadsafe(_xray_mgr.restart(),_loop)
        return jsonify({"ok":True})
    if os.path.exists(_xray_mgr._config_path):
        with open(_xray_mgr._config_path) as f:
            return jsonify(json.load(f))
    inbounds=_run_async(_db.get_xray_inbounds())
    outbounds=_run_async(_db.get_xray_outbounds())
    clients=_run_async(_db.get_xray_clients())
    return jsonify(_xray_mgr._build_config(inbounds,outbounds,clients))

def start_panel(cfg, db, inbound_mgr, traffic_ctrl, loop, router=None, xray_mgr=None):
    global _panel_cfg,_db,_inbound_mgr,_traffic_ctrl,_loop,_router,_xray_mgr
    _panel_cfg=cfg
    _db=db
    _inbound_mgr=inbound_mgr
    _traffic_ctrl=traffic_ctrl
    _loop=loop
    _router=router
    _xray_mgr=xray_mgr
    logging.getLogger().addHandler(_panel_handler)
    pfx=f"/{cfg.panel_path}" if cfg.panel_path else ""
    for path,methods,func in _routes:
        app.route(f"{pfx}{path}",methods=methods)(func)
    logger.info(f"panel at http://{cfg.panel_host}:{cfg.panel_port}{pfx}/")
    t=threading.Thread(target=lambda: waitress_serve(app,host=cfg.panel_host,port=cfg.panel_port,threads=cfg.panel_threads),daemon=True)
    t.start()
    return t
