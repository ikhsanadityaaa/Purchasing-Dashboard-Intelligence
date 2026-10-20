"""Fail-closed local network guard with scoped, Google-only Sheets sync.

Only the trusted Sheets sync thread may open HTTPS connections to an approved
Google endpoint. Normal dashboard reads and all other server threads remain
strictly loopback-only. DNS resolutions are tracked per thread, so a random IP
cannot be accepted merely because it uses HTTPS port 443.
"""
from contextlib import contextmanager
import ipaddress
import os
import socket
import threading
from urllib.parse import urlsplit

_original_connect=socket.socket.connect
_original_connect_ex=socket.socket.connect_ex
_original_getaddrinfo=socket.getaddrinfo
_installed=False
_scope=threading.local()
_APPROVED={'docs.google.com','sheets.googleapis.com','oauth2.googleapis.com','www.googleapis.com','drive.usercontent.google.com'}

def _allowed_hostname(host):
    h=str(host).lower().rstrip('.')
    return h in _APPROVED or h.endswith('.googleusercontent.com')

def _proxy_hostnames():
    hosts=set()
    for name in ('HTTPS_PROXY','https_proxy'):
        if not os.environ.get(name):continue
        try:
            url=urlsplit(os.environ[name])
            if url.hostname:hosts.add(url.hostname.lower())
        except ValueError:pass
    return hosts

def google_sync_is_allowed():
    return getattr(_scope,'ips',None) is not None

@contextmanager
def permit_google_sheets_network():
    previous=getattr(_scope,'ips',None)
    _scope.ips=set()
    try:yield
    finally:_scope.ips=previous

def _loopback(host):
    try:return ipaddress.ip_address(host).is_loopback
    except (ValueError,TypeError):return str(host).lower()=='localhost'

def _authorize(address):
    if not isinstance(address,tuple):return True  # Unix/named socket
    host,port=address[:2]
    if _loopback(host):return True
    ips=getattr(_scope,'ips',None)
    if ips is None or (str(host),int(port)) not in ips:
        raise PermissionError('Desktop Dashboard blocks network traffic outside approved Google Sheets sync')
    return True

def install_local_only_guard():
    global _installed
    if _installed:return
    def resolve(host,port,*args,**kwargs):
        result=_original_getaddrinfo(host,port,*args,**kwargs)
        ips=getattr(_scope,'ips',None)
        proxy=str(host).lower() in _proxy_hostnames()
        approved=_allowed_hostname(host) and str(port) in ('443','https')
        if ips is not None and (approved or proxy):
            for entry in result:
                addr=entry[4]
                if isinstance(addr,tuple):ips.add((str(addr[0]),int(addr[1])))
        return result
    def connect(sock,address):
        _authorize(address)
        return _original_connect(sock,address)
    def connect_ex(sock,address):
        _authorize(address)
        return _original_connect_ex(sock,address)
    socket.getaddrinfo=resolve
    socket.socket.connect=connect
    socket.socket.connect_ex=connect_ex
    _installed=True
