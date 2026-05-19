#!/usr/bin/env python3.13
import aiosqlite
import json
import logging
from datetime import datetime, timezone

logger=logging.getLogger(__name__)

_CURRENT_VERSION=4

SCHEMA="""
CREATE TABLE IF NOT EXISTS inbounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag TEXT UNIQUE NOT NULL,
    port INTEGER NOT NULL,
    transport TEXT NOT NULL DEFAULT 'websocket',
    path TEXT DEFAULT '/gn',
    ssl_cert TEXT DEFAULT '',
    ssl_key TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    ext_host TEXT DEFAULT '',
    ext_port INTEGER DEFAULT 0,
    ext_tls INTEGER DEFAULT 0,
    host TEXT DEFAULT '',
    sni TEXT DEFAULT '',
    listen_ip TEXT DEFAULT '0.0.0.0',
    pool_size INTEGER DEFAULT 8,
    poll_connections INTEGER DEFAULT 4,
    ping_interval INTEGER DEFAULT 20,
    ping_timeout INTEGER DEFAULT 10,
    user_agent TEXT DEFAULT '',
    max_upload_bytes INTEGER DEFAULT 1048576,
    max_download_bytes INTEGER DEFAULT 1048576,
    min_download_ms INTEGER DEFAULT 0,
    poll_min_connections INTEGER DEFAULT 1,
    ws_send_batch_bytes INTEGER DEFAULT 65536,
    sockopt_mark INTEGER DEFAULT 0,
    sockopt_tcp_fast_open INTEGER DEFAULT 0,
    sockopt_tcp_no_delay INTEGER DEFAULT 0,
    sockopt_tcp_keep_alive INTEGER DEFAULT 0,
    sockopt_tcp_congestion TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nanoid TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    inbound_tag TEXT NOT NULL,
    upload INTEGER DEFAULT 0,
    download INTEGER DEFAULT 0,
    traffic_limit INTEGER DEFAULT 0,
    expire_date TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    reset_at TEXT DEFAULT '',
    FOREIGN KEY(inbound_tag) REFERENCES inbounds(tag) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS outbounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL DEFAULT 'direct',
    config TEXT DEFAULT '{}',
    ord INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS routing_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ord INTEGER DEFAULT 0,
    domain TEXT DEFAULT '',
    ip TEXT DEFAULT '',
    port TEXT DEFAULT '',
    protocol TEXT DEFAULT '',
    inbound_tag TEXT DEFAULT '',
    outbound_tag TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    FOREIGN KEY(outbound_tag) REFERENCES outbounds(tag) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS xray_inbounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag TEXT UNIQUE NOT NULL,
    port INTEGER NOT NULL,
    protocol TEXT NOT NULL DEFAULT 'vless',
    settings TEXT DEFAULT '{}',
    stream_settings TEXT DEFAULT '{}',
    enabled INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS xray_outbounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag TEXT UNIQUE NOT NULL,
    protocol TEXT NOT NULL DEFAULT 'vmess',
    settings TEXT DEFAULT '{}',
    stream_settings TEXT DEFAULT '{}',
    ord INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS xray_clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    inbound_tag TEXT NOT NULL,
    upload INTEGER DEFAULT 0,
    download INTEGER DEFAULT 0,
    traffic_limit INTEGER DEFAULT 0,
    expire_date TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY(inbound_tag) REFERENCES xray_inbounds(tag) ON DELETE CASCADE
);
"""

_MIGRATIONS=[
    [
        "ALTER TABLE outbounds ADD COLUMN ord INTEGER DEFAULT 0",
        "ALTER TABLE inbounds ADD COLUMN ext_host TEXT DEFAULT ''",
        "ALTER TABLE inbounds ADD COLUMN ext_port INTEGER DEFAULT 0",
        "ALTER TABLE inbounds ADD COLUMN ext_tls INTEGER DEFAULT 0",
        "ALTER TABLE inbounds ADD COLUMN host TEXT DEFAULT ''",
        "ALTER TABLE inbounds ADD COLUMN sni TEXT DEFAULT ''",
        "ALTER TABLE inbounds ADD COLUMN listen_ip TEXT DEFAULT '0.0.0.0'",
        "ALTER TABLE routing_rules ADD COLUMN enabled INTEGER DEFAULT 1",
    ],
    [
        "ALTER TABLE inbounds ADD COLUMN pool_size INTEGER DEFAULT 8",
        "ALTER TABLE inbounds ADD COLUMN poll_connections INTEGER DEFAULT 4",
        "ALTER TABLE inbounds ADD COLUMN ping_interval INTEGER DEFAULT 20",
        "ALTER TABLE inbounds ADD COLUMN ping_timeout INTEGER DEFAULT 10",
        "ALTER TABLE inbounds ADD COLUMN user_agent TEXT DEFAULT ''",
    ],
    [
        "ALTER TABLE inbounds ADD COLUMN max_upload_bytes INTEGER DEFAULT 1048576",
        "ALTER TABLE inbounds ADD COLUMN max_download_bytes INTEGER DEFAULT 1048576",
        "ALTER TABLE inbounds ADD COLUMN min_download_ms INTEGER DEFAULT 0",
        "ALTER TABLE inbounds ADD COLUMN poll_min_connections INTEGER DEFAULT 1",
        "ALTER TABLE inbounds ADD COLUMN ws_send_batch_bytes INTEGER DEFAULT 65536",
    ],
    [
        "ALTER TABLE inbounds ADD COLUMN sockopt_mark INTEGER DEFAULT 0",
        "ALTER TABLE inbounds ADD COLUMN sockopt_tcp_fast_open INTEGER DEFAULT 0",
        "ALTER TABLE inbounds ADD COLUMN sockopt_tcp_no_delay INTEGER DEFAULT 0",
        "ALTER TABLE inbounds ADD COLUMN sockopt_tcp_keep_alive INTEGER DEFAULT 0",
        "ALTER TABLE inbounds ADD COLUMN sockopt_tcp_congestion TEXT DEFAULT ''",
    ],
]

class Database:
    def __init__(self, path):
        self._path=path
        self._db=None

    async def init(self):
        self._db=await aiosqlite.connect(self._path)
        self._db.row_factory=aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        await self._run_migrations()
        await self._ensure_defaults()

    async def _add_fk_constraints(self):
        async with self._db.execute("PRAGMA foreign_key_list(routing_rules)") as cur:
            if bool(await cur.fetchone()):
                return
        logger.info("migrating schema: adding foreign key constraints")
        await self._db.execute("PRAGMA foreign_keys=OFF")
        await self._db.execute("DELETE FROM routing_rules WHERE outbound_tag NOT IN (SELECT tag FROM outbounds)")
        await self._db.execute("DELETE FROM clients WHERE inbound_tag NOT IN (SELECT tag FROM inbounds)")
        await self._db.execute("""CREATE TABLE clients_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nanoid TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            inbound_tag TEXT NOT NULL,
            upload INTEGER DEFAULT 0,
            download INTEGER DEFAULT 0,
            traffic_limit INTEGER DEFAULT 0,
            expire_date TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            reset_at TEXT DEFAULT '',
            FOREIGN KEY(inbound_tag) REFERENCES inbounds(tag) ON DELETE CASCADE
        )""")
        await self._db.execute("INSERT INTO clients_new SELECT id,nanoid,name,inbound_tag,upload,download,traffic_limit,expire_date,enabled,created_at,reset_at FROM clients")
        await self._db.execute("DROP TABLE clients")
        await self._db.execute("ALTER TABLE clients_new RENAME TO clients")
        await self._db.execute("""CREATE TABLE routing_rules_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ord INTEGER DEFAULT 0,
            domain TEXT DEFAULT '',
            ip TEXT DEFAULT '',
            port TEXT DEFAULT '',
            protocol TEXT DEFAULT '',
            inbound_tag TEXT DEFAULT '',
            outbound_tag TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            FOREIGN KEY(outbound_tag) REFERENCES outbounds(tag) ON DELETE CASCADE
        )""")
        await self._db.execute("INSERT INTO routing_rules_new(id,ord,domain,ip,port,protocol,inbound_tag,outbound_tag) SELECT id,ord,domain,ip,port,protocol,inbound_tag,outbound_tag FROM routing_rules")
        await self._db.execute("DROP TABLE routing_rules")
        await self._db.execute("ALTER TABLE routing_rules_new RENAME TO routing_rules")
        await self._db.commit()
        await self._db.execute("PRAGMA foreign_keys=ON")
        logger.info("schema migration complete")

    async def _run_migrations(self):
        async with self._db.execute("PRAGMA user_version") as cur:
            version=(await cur.fetchone())[0]
        if version==0:
            await self._add_fk_constraints()
        for i,stmts in enumerate(_MIGRATIONS[version:],start=version):
            logger.info(f"migrating schema v{i} → v{i+1}")
            for stmt in stmts:
                try:
                    await self._db.execute(stmt)
                except Exception:
                    pass
            await self._db.execute(f"PRAGMA user_version={i+1}")
            await self._db.commit()

    async def _ensure_defaults(self):
        async with self._db.execute("SELECT tag FROM outbounds WHERE tag='direct'") as cur:
            if not await cur.fetchone():
                await self._db.execute("INSERT INTO outbounds(tag,type,config) VALUES('direct','direct','{}')")
        async with self._db.execute("SELECT tag FROM outbounds WHERE tag='block'") as cur:
            if not await cur.fetchone():
                await self._db.execute("INSERT INTO outbounds(tag,type,config) VALUES('block','block','{}')")
        await self._db.commit()

    async def close(self):
        if self._db:
            await self._db.close()

    async def get_setting(self, key, default=""):
        async with self._db.execute("SELECT value FROM settings WHERE key=?",(key,)) as cur:
            row=await cur.fetchone()
            return row["value"] if row else default

    async def set_setting(self, key, value):
        await self._db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",(key,str(value)))
        await self._db.commit()

    async def get_inbounds(self):
        async with self._db.execute("SELECT * FROM inbounds ORDER BY id") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_inbound(self, id):
        async with self._db.execute("SELECT * FROM inbounds WHERE id=?",(id,)) as cur:
            row=await cur.fetchone()
            return dict(row) if row else None

    async def create_inbound(self, tag, port, transport="websocket", path="/gn", ssl_cert="", ssl_key="", listen_ip="0.0.0.0", ext_host="", ext_port=0, ext_tls=0, host="", sni="", pool_size=8, poll_connections=4, ping_interval=20, ping_timeout=10, user_agent="", max_upload_bytes=1048576, max_download_bytes=1048576, min_download_ms=0, poll_min_connections=1, ws_send_batch_bytes=65536, sockopt_mark=0, sockopt_tcp_fast_open=0, sockopt_tcp_no_delay=0, sockopt_tcp_keep_alive=0, sockopt_tcp_congestion=""):
        await self._db.execute(
            "INSERT INTO inbounds(tag,port,transport,path,ssl_cert,ssl_key,listen_ip,ext_host,ext_port,ext_tls,host,sni,pool_size,poll_connections,ping_interval,ping_timeout,user_agent,max_upload_bytes,max_download_bytes,min_download_ms,poll_min_connections,ws_send_batch_bytes,sockopt_mark,sockopt_tcp_fast_open,sockopt_tcp_no_delay,sockopt_tcp_keep_alive,sockopt_tcp_congestion) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tag,port,transport,path,ssl_cert,ssl_key,listen_ip,ext_host,ext_port,ext_tls,host,sni,pool_size,poll_connections,ping_interval,ping_timeout,user_agent,max_upload_bytes,max_download_bytes,min_download_ms,poll_min_connections,ws_send_batch_bytes,sockopt_mark,sockopt_tcp_fast_open,sockopt_tcp_no_delay,sockopt_tcp_keep_alive,sockopt_tcp_congestion)
        )
        await self._db.commit()
        async with self._db.execute("SELECT last_insert_rowid()") as cur:
            return (await cur.fetchone())[0]

    async def update_inbound(self, id, **kwargs):
        fields=",".join(f"{k}=?" for k in kwargs)
        await self._db.execute(f"UPDATE inbounds SET {fields} WHERE id=?",(*kwargs.values(),id))
        await self._db.commit()

    async def delete_inbound(self, id):
        async with self._db.execute("SELECT tag FROM inbounds WHERE id=?",(id,)) as cur:
            row=await cur.fetchone()
        if row:
            await self._db.execute("DELETE FROM routing_rules WHERE inbound_tag=?",(row["tag"],))
        await self._db.execute("DELETE FROM inbounds WHERE id=?",(id,))
        await self._db.commit()

    async def get_clients(self, inbound_tag=None):
        if inbound_tag:
            async with self._db.execute("SELECT * FROM clients WHERE inbound_tag=? ORDER BY id",(inbound_tag,)) as cur:
                return [dict(r) for r in await cur.fetchall()]
        async with self._db.execute("SELECT * FROM clients ORDER BY id") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_client(self, id):
        async with self._db.execute("SELECT * FROM clients WHERE id=?",(id,)) as cur:
            row=await cur.fetchone()
            return dict(row) if row else None

    async def get_client_by_nanoid(self, nanoid):
        async with self._db.execute("SELECT * FROM clients WHERE nanoid=?",(nanoid,)) as cur:
            row=await cur.fetchone()
            return dict(row) if row else None

    async def create_client(self, nanoid, name, inbound_tag, traffic_limit=0, expire_date=""):
        now=datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO clients(nanoid,name,inbound_tag,traffic_limit,expire_date,created_at) VALUES(?,?,?,?,?,?)",
            (nanoid,name,inbound_tag,traffic_limit,expire_date,now)
        )
        await self._db.commit()
        async with self._db.execute("SELECT last_insert_rowid()") as cur:
            return (await cur.fetchone())[0]

    async def update_client(self, id, **kwargs):
        fields=",".join(f"{k}=?" for k in kwargs)
        await self._db.execute(f"UPDATE clients SET {fields} WHERE id=?",(*kwargs.values(),id))
        await self._db.commit()

    async def delete_client(self, id):
        await self._db.execute("DELETE FROM clients WHERE id=?",(id,))
        await self._db.commit()

    async def disable_client(self, id):
        await self._db.execute("UPDATE clients SET enabled=0 WHERE id=?",(id,))
        await self._db.commit()

    async def add_traffic(self, nanoid, upload, download):
        await self._db.execute(
            "UPDATE clients SET upload=upload+?,download=download+? WHERE nanoid=?",
            (upload,download,nanoid)
        )
        await self._db.commit()

    async def reset_client_traffic(self, id):
        now=datetime.now(timezone.utc).isoformat()
        await self._db.execute("UPDATE clients SET upload=0,download=0,reset_at=? WHERE id=?",(now,id))
        await self._db.commit()

    async def get_outbounds(self):
        async with self._db.execute("SELECT * FROM outbounds ORDER BY ord,id") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_outbound(self, id):
        async with self._db.execute("SELECT * FROM outbounds WHERE id=?",(id,)) as cur:
            row=await cur.fetchone()
            return dict(row) if row else None

    async def create_outbound(self, tag, type_, config="{}"):
        await self._db.execute("INSERT INTO outbounds(tag,type,config) VALUES(?,?,?)",(tag,type_,config))
        await self._db.commit()
        async with self._db.execute("SELECT last_insert_rowid()") as cur:
            return (await cur.fetchone())[0]

    async def update_outbound(self, id, **kwargs):
        fields=",".join(f"{k}=?" for k in kwargs)
        await self._db.execute(f"UPDATE outbounds SET {fields} WHERE id=?",(*kwargs.values(),id))
        await self._db.commit()

    async def delete_outbound(self, id):
        await self._db.execute("DELETE FROM outbounds WHERE id=?",(id,))
        await self._db.commit()

    async def get_routing_rules(self):
        async with self._db.execute("SELECT * FROM routing_rules ORDER BY ord,id") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_routing_rule(self, id):
        async with self._db.execute("SELECT * FROM routing_rules WHERE id=?",(id,)) as cur:
            row=await cur.fetchone()
            return dict(row) if row else None

    async def create_routing_rule(self, outbound_tag, domain="", ip="", port="", protocol="", inbound_tag="", ord=0):
        await self._db.execute(
            "INSERT INTO routing_rules(ord,domain,ip,port,protocol,inbound_tag,outbound_tag) VALUES(?,?,?,?,?,?,?)",
            (ord,domain,ip,port,protocol,inbound_tag,outbound_tag)
        )
        await self._db.commit()
        async with self._db.execute("SELECT last_insert_rowid()") as cur:
            return (await cur.fetchone())[0]

    async def update_routing_rule(self, id, **kwargs):
        fields=",".join(f"{k}=?" for k in kwargs)
        await self._db.execute(f"UPDATE routing_rules SET {fields} WHERE id=?",(*kwargs.values(),id))
        await self._db.commit()

    async def delete_routing_rule(self, id):
        await self._db.execute("DELETE FROM routing_rules WHERE id=?",(id,))
        await self._db.commit()

    async def reorder_outbounds(self, order_list):
        for item in order_list:
            await self._db.execute("UPDATE outbounds SET ord=? WHERE id=?",(item["ord"],item["id"]))
        await self._db.commit()

    async def reorder_rules(self, order_list):
        for item in order_list:
            await self._db.execute("UPDATE routing_rules SET ord=? WHERE id=?",(item["ord"],item["id"]))
        await self._db.commit()

    async def get_xray_inbounds(self):
        async with self._db.execute("SELECT * FROM xray_inbounds ORDER BY id") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_xray_inbound(self, id):
        async with self._db.execute("SELECT * FROM xray_inbounds WHERE id=?",(id,)) as cur:
            row=await cur.fetchone()
            return dict(row) if row else None

    async def create_xray_inbound(self, tag, port, protocol="vless", settings="{}", stream_settings="{}"):
        await self._db.execute(
            "INSERT INTO xray_inbounds(tag,port,protocol,settings,stream_settings) VALUES(?,?,?,?,?)",
            (tag,port,protocol,settings,stream_settings)
        )
        await self._db.commit()
        async with self._db.execute("SELECT last_insert_rowid()") as cur:
            return (await cur.fetchone())[0]

    async def update_xray_inbound(self, id, **kwargs):
        fields=",".join(f"{k}=?" for k in kwargs)
        await self._db.execute(f"UPDATE xray_inbounds SET {fields} WHERE id=?",(*kwargs.values(),id))
        await self._db.commit()

    async def delete_xray_inbound(self, id):
        await self._db.execute("DELETE FROM xray_inbounds WHERE id=?",(id,))
        await self._db.commit()

    async def get_xray_outbounds(self):
        async with self._db.execute("SELECT * FROM xray_outbounds ORDER BY ord,id") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_xray_outbound(self, id):
        async with self._db.execute("SELECT * FROM xray_outbounds WHERE id=?",(id,)) as cur:
            row=await cur.fetchone()
            return dict(row) if row else None

    async def create_xray_outbound(self, tag, protocol, settings="{}", stream_settings="{}"):
        await self._db.execute(
            "INSERT INTO xray_outbounds(tag,protocol,settings,stream_settings) VALUES(?,?,?,?)",
            (tag,protocol,settings,stream_settings)
        )
        await self._db.commit()
        async with self._db.execute("SELECT last_insert_rowid()") as cur:
            return (await cur.fetchone())[0]

    async def update_xray_outbound(self, id, **kwargs):
        fields=",".join(f"{k}=?" for k in kwargs)
        await self._db.execute(f"UPDATE xray_outbounds SET {fields} WHERE id=?",(*kwargs.values(),id))
        await self._db.commit()

    async def delete_xray_outbound(self, id):
        await self._db.execute("DELETE FROM xray_outbounds WHERE id=?",(id,))
        await self._db.commit()

    async def get_xray_clients(self, inbound_tag=None):
        if inbound_tag:
            async with self._db.execute("SELECT * FROM xray_clients WHERE inbound_tag=? ORDER BY id",(inbound_tag,)) as cur:
                return [dict(r) for r in await cur.fetchall()]
        async with self._db.execute("SELECT * FROM xray_clients ORDER BY id") as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_xray_client(self, id):
        async with self._db.execute("SELECT * FROM xray_clients WHERE id=?",(id,)) as cur:
            row=await cur.fetchone()
            return dict(row) if row else None

    async def create_xray_client(self, uuid, name, inbound_tag, traffic_limit=0, expire_date=""):
        now=datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO xray_clients(uuid,name,inbound_tag,traffic_limit,expire_date,created_at) VALUES(?,?,?,?,?,?)",
            (uuid,name,inbound_tag,traffic_limit,expire_date,now)
        )
        await self._db.commit()
        async with self._db.execute("SELECT last_insert_rowid()") as cur:
            return (await cur.fetchone())[0]

    async def update_xray_client(self, id, **kwargs):
        fields=",".join(f"{k}=?" for k in kwargs)
        await self._db.execute(f"UPDATE xray_clients SET {fields} WHERE id=?",(*kwargs.values(),id))
        await self._db.commit()

    async def delete_xray_client(self, id):
        await self._db.execute("DELETE FROM xray_clients WHERE id=?",(id,))
        await self._db.commit()

    async def add_xray_traffic(self, uuid, upload, download):
        await self._db.execute(
            "UPDATE xray_clients SET upload=upload+?,download=download+? WHERE uuid=?",
            (upload,download,uuid)
        )
        await self._db.commit()
