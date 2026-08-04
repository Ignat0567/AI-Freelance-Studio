from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit


_BRACKETED_HOST = re.compile(r"^\[([^\]]+)\]:(\d{1,5})$")


def is_loopback_host(value: str) -> bool:
    candidate = str(value or "").strip().lower().rstrip(".")
    if candidate == "localhost":
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def parse_host_header(value: str) -> tuple[str, int] | None:
    candidate = str(value or "").strip()
    bracketed = _BRACKETED_HOST.fullmatch(candidate)
    if bracketed:
        host, raw_port = bracketed.groups()
    else:
        if candidate.count(":") != 1:
            return None
        host, raw_port = candidate.rsplit(":", 1)
    if not host or not raw_port.isdigit():
        return None
    port = int(raw_port)
    if not 1 <= port <= 65535:
        return None
    return host.lower().rstrip("."), port


def host_matches_context(value: str, bind_host: str, port: int) -> bool:
    parsed = parse_host_header(value)
    if parsed is None:
        return False
    host, supplied_port = parsed
    if supplied_port != port:
        return False
    normalized_bind = bind_host.lower().rstrip(".")
    if is_loopback_host(bind_host):
        return host == normalized_bind
    if bind_host in {"0.0.0.0", "::"}:
        return is_loopback_host(host)
    return host == normalized_bind


def exact_origin_allowed(value: str, allowed_origins: frozenset[str]) -> bool:
    if not value or value == "null" or value not in allowed_origins:
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.path == "" and not parsed.query and not parsed.fragment
