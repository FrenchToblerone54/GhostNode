#!/usr/bin/env python3.13
import tomllib
import os

DEFAULT_CONFIG_PATH="/etc/ghostnode/config.toml"
VERSION="v0.1.5"

DEFAULT_TOML="""[panel]
enabled = true
host = "127.0.0.1"
port = 9090
path = "ghostnode-panel"
threads = 4

[database]
path = "/etc/ghostnode/ghostnode.db"

[geo]
geoip_path = "/etc/ghostnode/geo/GeoLite2-Country.mmdb"
geoip_dat_path = "/etc/ghostnode/geo/geoip.dat"
geosite_path = "/etc/ghostnode/geo/geosite.dat"

[logging]
level = "info"
file = "/var/log/ghostnode.log"

[server]
auto_update = true
update_check_interval = 300
update_check_on_startup = true
update_proxy = ""
hostname = ""
"""

class GhostNodeConfig:
    def __init__(self, config_path=None):
        self.config_path=config_path or DEFAULT_CONFIG_PATH
        raw={}
        if os.path.exists(self.config_path):
            with open(self.config_path,"rb") as f:
                raw=tomllib.load(f)
        panel=raw.get("panel",{})
        self.panel_enabled=panel.get("enabled",True)
        self.panel_host=panel.get("host","127.0.0.1")
        self.panel_port=panel.get("port",9090)
        self.panel_path=panel.get("path","ghostnode-panel")
        self.panel_threads=panel.get("threads",4)
        db=raw.get("database",{})
        self.db_path=db.get("path","/etc/ghostnode/ghostnode.db")
        geo=raw.get("geo",{})
        self.geoip_path=geo.get("geoip_path","/etc/ghostnode/geo/GeoLite2-Country.mmdb")
        self.geoip_dat_path=geo.get("geoip_dat_path","/etc/ghostnode/geo/geoip.dat")
        self.geosite_path=geo.get("geosite_path","/etc/ghostnode/geo/geosite.dat")
        log=raw.get("logging",{})
        self.log_level=log.get("level","info")
        self.log_file=log.get("file","/var/log/ghostnode.log")
        srv=raw.get("server",{})
        self.auto_update=srv.get("auto_update",True)
        self.update_check_interval=srv.get("update_check_interval",300)
        self.update_check_on_startup=srv.get("update_check_on_startup",True)
        self.update_proxy=srv.get("update_proxy","")
        self.hostname=srv.get("hostname","")

def write_default_config(path):
    os.makedirs(os.path.dirname(path),exist_ok=True)
    with open(path,"w") as f:
        f.write(DEFAULT_TOML)
