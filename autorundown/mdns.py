"""Announce the server on the local network as http://autorundown.local:<port>.

mDNS (multicast DNS, "Bonjour") is how printers get names without anyone
touching the router: the PC answers queries for `autorundown.local` itself,
so a phone can bookmark a name that survives the PC getting a new IP
address. iPhones resolve .local names; Android does from version 12 on in
Chrome. Guest networks and some mesh routers block multicast -- then the
numeric LAN address still works.

Best effort by design: a failure here (no zeroconf package, port 5353 not
available, the name already taken by another PC) is logged and the server
runs on regardless.
"""
from __future__ import annotations

import socket

HOSTNAME = "autorundown"


class Announcer:
    def __init__(self, port: int, ip: str, name: str = HOSTNAME):
        self.port, self.ip, self.name = port, ip, name
        self.zc = None
        self.info = None
        self.error = ""

    @property
    def url(self) -> str:
        return f"http://{self.name}.local:{self.port}" if self.zc else ""

    def start(self) -> "Announcer":
        if self.ip.startswith("127."):
            self.error = "no LAN address"
            return self
        try:
            from zeroconf import IPVersion, ServiceInfo, Zeroconf
            info = ServiceInfo(
                "_http._tcp.local.", f"AutoRundown._http._tcp.local.",
                addresses=[socket.inet_aton(self.ip)], port=self.port,
                server=f"{self.name}.local.", properties={"path": "/"})
            zc = Zeroconf(ip_version=IPVersion.V4Only)
            try:
                # registering a service with server=<name>.local. makes
                # zeroconf answer address queries for that name
                zc.register_service(info, allow_name_change=True)
            except Exception:
                zc.close()
                raise
            self.zc, self.info = zc, info
        except Exception as exc:   # never let the name stop the server
            self.error = f"{type(exc).__name__}: {exc}"
        return self

    def close(self) -> None:
        if self.zc:
            try:
                self.zc.unregister_service(self.info)
            finally:
                self.zc.close()
                self.zc = None
