#!/usr/bin/env python3.13
import asyncio
import json
import logging
import os
import platform
import random
import signal
import subprocess
import tempfile
import time
import urllib.request
import zipfile
import aiohttp

logger=logging.getLogger(__name__)

XRAY_DOWNLOAD_BASE="https://github.com/XTLS/Xray-core/releases/latest/download"

class XrayManager:
    def __init__(self, xray_path="/usr/local/bin/xray", config_path="/etc/ghostnode/xray.json", socks_port=10808):
        _xray_fallback=os.path.expanduser("~/.local/bin/xray")
        _cfg_fallback=os.path.expanduser("~/.local/share/ghostnode/xray.json")
        self._xray_path=xray_path if (os.path.isfile(xray_path) and os.access(xray_path, os.X_OK)) else (_xray_fallback if os.path.isfile(_xray_fallback) else xray_path)
        self._config_path=config_path if os.path.isfile(config_path) else (_cfg_fallback if os.path.isfile(_cfg_fallback) else config_path)
        self._socks_port=socks_port
        self._process=None
        self._db=None

    def set_db(self, db):
        self._db=db

    def is_installed(self):
        return os.path.isfile(self._xray_path) and os.access(self._xray_path, os.X_OK)

    async def get_version(self):
        if not self.is_installed():
            return None
        try:
            proc=await asyncio.create_subprocess_exec(
                self._xray_path, "version",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            out, _=await asyncio.wait_for(proc.communicate(), timeout=5)
            first_line=out.decode().splitlines()[0] if out else ""
            parts=first_line.split()
            return parts[1] if len(parts)>=2 else first_line.strip()
        except Exception:
            return None

    def install(self, http_proxy=""):
        arch=platform.machine()
        if arch in ("aarch64","arm64"):
            fname="Xray-linux-arm64-v8a.zip"
        elif arch=="armv7l":
            fname="Xray-linux-arm32-v7a.zip"
        else:
            fname="Xray-linux-64.zip"
        url=f"{XRAY_DOWNLOAD_BASE}/{fname}"
        logger.info(f"downloading Xray from {url}")
        self._installing=True
        self._install_error=None
        self._install_progress={"stage":"downloading","bytes":0,"total":0}
        try:
            opener=urllib.request.build_opener()
            if http_proxy:
                opener.add_handler(urllib.request.ProxyHandler({"http":http_proxy,"https":http_proxy}))
            with opener.open(urllib.request.Request(url,headers={"User-Agent":"GhostNode"}),timeout=300) as resp:
                total=int(resp.headers.get("Content-Length",0))
                self._install_progress["total"]=total
                data=bytearray()
                while True:
                    chunk=resp.read(65536)
                    if not chunk:
                        break
                    data.extend(chunk)
                    self._install_progress["bytes"]=len(data)
            self._install_progress["stage"]="extracting"
            with tempfile.NamedTemporaryFile(suffix=".zip",delete=False) as f:
                f.write(data)
                tmpzip=f.name
            try:
                with zipfile.ZipFile(tmpzip) as zf:
                    names=zf.namelist()
                    xray_name=next((n for n in names if os.path.basename(n)=="xray"),None)
                    if not xray_name:
                        raise RuntimeError("xray binary not found in zip")
                    xray_data=zf.read(xray_name)
                try:
                    os.makedirs(os.path.dirname(self._xray_path),exist_ok=True)
                    with open(self._xray_path,"wb") as f:
                        f.write(xray_data)
                    os.chmod(self._xray_path,0o755)
                except PermissionError:
                    fallback=os.path.expanduser("~/.local/bin/xray")
                    os.makedirs(os.path.dirname(fallback),exist_ok=True)
                    with open(fallback,"wb") as f:
                        f.write(xray_data)
                    os.chmod(fallback,0o755)
                    self._xray_path=fallback
                logger.info(f"Xray installed to {self._xray_path}")
                self._install_progress["stage"]="done"
            finally:
                os.unlink(tmpzip)
        except Exception as e:
            self._install_error=str(e)
            self._install_progress["stage"]="error"
            logger.error(f"Xray install failed: {e}")
            raise
        finally:
            self._installing=False

    def _build_config(self, inbounds, outbounds, clients):
        xray_inbounds=[]
        for inb in inbounds:
            if not inb.get("enabled", 1):
                continue
            inb_clients=[c for c in clients if c["inbound_tag"]==inb["tag"] and c.get("enabled", 1)]
            settings=json.loads(inb.get("settings", "{}")) if isinstance(inb.get("settings"), str) else inb.get("settings", {})
            stream=json.loads(inb.get("stream_settings", "{}")) if isinstance(inb.get("stream_settings"), str) else inb.get("stream_settings", {})
            if inb["protocol"] in ("vless", "vmess", "trojan"):
                if inb["protocol"]=="vless":
                    settings.setdefault("decryption", "none")
                    settings["clients"]=[{"id": c["uuid"], "email": c["name"]} for c in inb_clients]
                elif inb["protocol"]=="vmess":
                    settings["clients"]=[{"id": c["uuid"], "alterId": 0, "email": c["name"]} for c in inb_clients]
                elif inb["protocol"]=="trojan":
                    settings["clients"]=[{"password": c["uuid"], "email": c["name"]} for c in inb_clients]
            xray_inbounds.append({"tag": inb["tag"], "port": inb["port"], "protocol": inb["protocol"], "settings": settings, "streamSettings": stream})
        xray_inbounds.append({"tag": "_gn_socks", "port": self._socks_port, "protocol": "socks", "settings": {"auth": "noauth", "udp": True}, "listen": "127.0.0.1"})
        xray_outbounds=[{"tag": "direct", "protocol": "freedom"}, {"tag": "block", "protocol": "blackhole"}]
        for ob in outbounds:
            settings=json.loads(ob.get("settings", "{}")) if isinstance(ob.get("settings"), str) else ob.get("settings", {})
            stream=json.loads(ob.get("stream_settings", "{}")) if isinstance(ob.get("stream_settings"), str) else ob.get("stream_settings", {})
            xray_outbounds.append({"tag": ob["tag"], "protocol": ob["protocol"], "settings": settings, "streamSettings": stream})
        return {"log": {"loglevel": "warning"}, "inbounds": xray_inbounds, "outbounds": xray_outbounds}

    async def write_config(self):
        if not self._db:
            return
        inbounds=await self._db.get_xray_inbounds()
        outbounds=await self._db.get_xray_outbounds()
        clients=await self._db.get_xray_clients()
        cfg=self._build_config(inbounds, outbounds, clients)
        try:
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            with open(self._config_path, "w") as f:
                json.dump(cfg, f, indent=2)
        except PermissionError:
            fallback=os.path.expanduser("~/.local/share/ghostnode/xray.json")
            os.makedirs(os.path.dirname(fallback), exist_ok=True)
            with open(fallback, "w") as f:
                json.dump(cfg, f, indent=2)
            self._config_path=fallback
            logger.info(f"Xray config written to fallback path {fallback}")
        except OSError as e:
            logger.error(f"cannot write Xray config to {self._config_path}: {e}")

    def is_running(self):
        return self._process is not None and self._process.poll() is None

    async def start(self):
        if self.is_running():
            return
        if not self.is_installed():
            logger.warning("Xray not installed, cannot start")
            return
        await self.write_config()
        if not os.path.exists(self._config_path):
            logger.error(f"Xray config missing at {self._config_path}, cannot start")
            return
        self._process=subprocess.Popen(
            [self._xray_path, "run", "-c", self._config_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        logger.info(f"Xray started (pid {self._process.pid})")

    async def stop(self):
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(self._process.wait), timeout=5)
            except Exception:
                self._process.kill()
        self._process=None
        logger.info("Xray stopped")

    async def restart(self):
        await self.stop()
        await self.start()

    async def test_outbound(self, outbound_config, http_proxy=""):
        tmp_port=random.randint(40000, 49999)
        settings=outbound_config.get("settings", {})
        stream=outbound_config.get("stream_settings", {})
        cfg={
            "log": {"loglevel": "none"},
            "inbounds": [{"tag": "http_test", "port": tmp_port, "protocol": "http", "listen": "127.0.0.1"}],
            "outbounds": [
                {"tag": "test_ob", "protocol": outbound_config["protocol"], "settings": settings, "streamSettings": stream},
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "block", "protocol": "blackhole"}
            ]
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(cfg, f)
            tmp_cfg=f.name
        proc=None
        try:
            proc=subprocess.Popen(
                [self._xray_path, "run", "-c", tmp_cfg],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            await asyncio.sleep(1.0)
            if proc.poll() is not None:
                return None
            start=time.monotonic()
            timeout=aiohttp.ClientTimeout(total=10)
            proxy_url=f"http://127.0.0.1:{tmp_port}"
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get("https://www.google.com/generate_204", proxy=proxy_url) as resp:
                    await resp.read()
                    latency_ms=int((time.monotonic()-start)*1000)
                    return latency_ms if resp.status in (204, 200) else None
        except Exception as e:
            logger.debug(f"xray outbound test failed: {e}")
            return None
        finally:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
            try:
                os.unlink(tmp_cfg)
            except Exception:
                pass
