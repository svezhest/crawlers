"""Credentials + proxy from the environment / ./.env (never from the repo)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv(os.path.join(os.getcwd(), ".env"))


@dataclass(frozen=True)
class Config:
    api_id: int | None
    api_hash: str | None
    session: str

    @staticmethod
    def load() -> "Config":
        api_id = os.getenv("TG_API_ID")
        return Config(api_id=int(api_id) if api_id else None, api_hash=os.getenv("TG_API_HASH"),
                      session=os.getenv("TG_SESSION", "telegram_crawler.session"))

    def require(self) -> "Config":
        if self.api_id and self.api_hash:
            return self
        raise SystemExit("Set TG_API_ID / TG_API_HASH (https://my.telegram.org) in .env or the environment.")

    def proxy(self):
        import socks
        url = (os.getenv("TG_PROXY") or os.getenv("ALL_PROXY") or os.getenv("HTTPS_PROXY")
               or os.getenv("https_proxy") or os.getenv("HTTP_PROXY") or os.getenv("http_proxy"))
        if not url:
            return None
        if "://" not in url:
            url = "http://" + url
        p = urlparse(url)
        ptype = {"socks5": socks.SOCKS5, "socks5h": socks.SOCKS5, "socks4": socks.SOCKS4,
                 "http": socks.HTTP, "https": socks.HTTP}.get((p.scheme or "http").lower(), socks.HTTP)
        if not p.hostname or not p.port:
            return None
        d = {"proxy_type": ptype, "addr": p.hostname, "port": p.port}
        if p.username:
            d["username"], d["password"] = p.username, p.password or ""
        return d
