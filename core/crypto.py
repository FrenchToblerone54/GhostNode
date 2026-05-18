#!/usr/bin/env python3.13
import asyncio
import base64
import hashlib
import os
import secrets
import struct
from concurrent.futures import ThreadPoolExecutor
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa,padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key

_executor=ThreadPoolExecutor(max_workers=os.cpu_count())
MSG_PUBKEY=0x00
MSG_AUTH=0x01
MSG_SESSION_KEY=0x0A
AUTH_SALT_SIZE=32
_server_private_key=None
_server_public_key=None

def init_server_keys():
    global _server_private_key,_server_public_key
    _server_private_key,_server_public_key=_gen_rsa_pair()

async def load_or_create_server_keys(db):
    global _server_private_key,_server_public_key
    pem=await db.get_setting("server_private_key","")
    if pem:
        _server_private_key=load_pem_private_key(pem.encode(),password=None)
        _server_public_key=_server_private_key.public_key()
    else:
        _server_private_key,_server_public_key=_gen_rsa_pair()
        pem=_server_private_key.private_bytes(encoding=serialization.Encoding.PEM,format=serialization.PrivateFormat.PKCS8,encryption_algorithm=serialization.NoEncryption()).decode()
        await db.set_setting("server_private_key",pem)

def get_server_fingerprint():
    der=_ser_pubkey(_server_public_key)
    return base64.urlsafe_b64encode(hashlib.sha256(der).digest()).decode().rstrip("=")

def _gen_rsa_pair():
    pk=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    return pk,pk.public_key()

def _ser_pubkey(k):
    return k.public_bytes(encoding=serialization.Encoding.DER,format=serialization.PublicFormat.SubjectPublicKeyInfo)

def _deser_pubkey(b):
    return serialization.load_der_public_key(b)

def _rsa_enc(pubkey,plain):
    return pubkey.encrypt(plain,padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),algorithm=hashes.SHA256(),label=None))

def _rsa_dec(privkey,cipher):
    return privkey.decrypt(cipher,padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),algorithm=hashes.SHA256(),label=None))

def _derive(token,salt):
    return hashlib.pbkdf2_hmac("sha256",token.encode(),salt,100000,32)

def _fingerprint(pubkey):
    der=_ser_pubkey(pubkey)
    return base64.urlsafe_b64encode(hashlib.sha256(der).digest()).decode().rstrip("=")

def _pack_msg(msg_type,payload):
    return struct.pack("!BII",msg_type,0,len(payload))+payload

async def server_handshake(reader,writer,db):
    loop=asyncio.get_running_loop()
    auth_salt=os.urandom(AUTH_SALT_SIZE)
    pubkey_bytes=_ser_pubkey(_server_public_key)
    writer.write(_pack_msg(MSG_PUBKEY,pubkey_bytes+auth_salt))
    await writer.drain()
    hdr=await reader.readexactly(9)
    msg_type,_,length=struct.unpack("!BII",hdr)
    if msg_type!=MSG_AUTH:
        raise ValueError("expected MSG_AUTH")
    enc_auth=bytes(await reader.readexactly(length))
    auth_payload=await loop.run_in_executor(_executor,_rsa_dec,_server_private_key,enc_auth)
    parts=auth_payload.split(b"\x00",2)
    if len(parts)!=3 or parts[0]!=b"gn":
        raise ValueError("invalid auth")
    nanoid=parts[1].decode()
    token_bytes=parts[2]
    expected=await loop.run_in_executor(_executor,_derive,nanoid,auth_salt)
    if not secrets.compare_digest(token_bytes,expected):
        raise ValueError("bad token")
    client=await db.get_client_by_nanoid(nanoid)
    if not client or not client["enabled"]:
        raise ValueError("client rejected")
    hdr=await reader.readexactly(9)
    msg_type,_,length=struct.unpack("!BII",hdr)
    if msg_type!=MSG_PUBKEY:
        raise ValueError("expected client pubkey")
    client_pubkey=_deser_pubkey(bytes(await reader.readexactly(length)))
    session_key=os.urandom(32)
    enc_key=await loop.run_in_executor(_executor,_rsa_enc,client_pubkey,session_key)
    writer.write(_pack_msg(MSG_SESSION_KEY,enc_key))
    await writer.drain()
    return EncryptedReader(reader,session_key),EncryptedWriter(writer,session_key),nanoid

async def client_handshake(reader,writer,nanoid,expected_fp=""):
    loop=asyncio.get_running_loop()
    hdr=await reader.readexactly(9)
    msg_type,_,length=struct.unpack("!BII",hdr)
    if msg_type!=MSG_PUBKEY:
        raise ValueError("expected server pubkey")
    payload=bytes(await reader.readexactly(length))
    server_pubkey=_deser_pubkey(payload[:-AUTH_SALT_SIZE])
    if expected_fp and _fingerprint(server_pubkey)!=expected_fp:
        raise ValueError("server fingerprint mismatch")
    auth_salt=payload[-AUTH_SALT_SIZE:]
    client_priv,client_pub=await loop.run_in_executor(_executor,_gen_rsa_pair)
    token_bytes=await loop.run_in_executor(_executor,_derive,nanoid,auth_salt)
    auth_payload=b"gn\x00"+nanoid.encode()+b"\x00"+token_bytes
    enc_auth=await loop.run_in_executor(_executor,_rsa_enc,server_pubkey,auth_payload)
    writer.write(_pack_msg(MSG_AUTH,enc_auth))
    await writer.drain()
    writer.write(_pack_msg(MSG_PUBKEY,_ser_pubkey(client_pub)))
    await writer.drain()
    hdr=await reader.readexactly(9)
    msg_type,_,length=struct.unpack("!BII",hdr)
    if msg_type!=MSG_SESSION_KEY:
        raise ValueError("expected session key")
    enc_key=bytes(await reader.readexactly(length))
    session_key=await loop.run_in_executor(_executor,_rsa_dec,client_priv,enc_key)
    return EncryptedReader(reader,session_key),EncryptedWriter(writer,session_key)

class EncryptedReader:
    def __init__(self,base,key):
        self._base=base
        self._key=key
        self._buf=bytearray()
        self._closed=False

    async def _next_frame(self):
        if self._closed:
            return
        try:
            length_bytes=await self._base.readexactly(4)
            length=struct.unpack("!I",length_bytes)[0]
            frame=bytes(await self._base.readexactly(length))
            nonce,ct=frame[:12],frame[12:]
            plain=AESGCM(self._key).decrypt(nonce,ct,b"")
            self._buf.extend(plain)
        except Exception:
            self._closed=True

    async def read(self,n=-1):
        if not self._buf and not self._closed:
            await self._next_frame()
        if not self._buf:
            return b""
        if n<0 or n>=len(self._buf):
            data=bytes(self._buf)
            self._buf.clear()
            return data
        data=bytes(self._buf[:n])
        del self._buf[:n]
        return data

    async def readexactly(self,n):
        while len(self._buf)<n and not self._closed:
            await self._next_frame()
        if len(self._buf)<n:
            raise asyncio.IncompleteReadError(bytes(self._buf),n)
        data=bytes(self._buf[:n])
        del self._buf[:n]
        return data

class EncryptedWriter:
    def __init__(self,base,key):
        self._base=base
        self._key=key
        self._buf=bytearray()
        self._closed=False

    def write(self,data):
        if not self._closed:
            self._buf.extend(data)

    async def drain(self):
        if not self._buf or self._closed:
            return
        data=bytes(self._buf)
        self._buf.clear()
        nonce=os.urandom(12)
        ct=AESGCM(self._key).encrypt(nonce,data,b"")
        frame=nonce+ct
        self._base.write(struct.pack("!I",len(frame))+frame)
        await self._base.drain()

    def close(self):
        self._closed=True
        try:
            self._base.close()
        except Exception:
            pass
