"""ASGI Reverse Proxy Middleware for Web Testing Harness.

Inspects incoming HTTP/WebSocket connections. When connecting from a trusted proxy
IP or CIDR range, extracts and validates:
  - X-Forwarded-For: Real client IP extraction
  - X-Forwarded-Proto: Scheme normalization (http vs https)
  - X-Forwarded-Host: Host header normalization
  - X-Forwarded-Prefix: Subpath root_path normalization
  - Stashes complete diagnostic proxy info on request.state.proxy
"""
import ipaddress
import logging
from typing import List, Optional, Union

_log = logging.getLogger("harness.proxy")

DEFAULT_PRIVATE_NETWORKS = [
    "127.0.0.1/32",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "fc00::/7",
    "localhost",
    "testclient",
]


class ReverseProxyMiddleware:
    def __init__(
        self,
        app,
        trusted_proxies: Optional[Union[List[str], str]] = None,
        trust_all: bool = False,
        trust_proto: bool = True,
        trust_host: bool = True,
        trust_prefix: bool = True,
        default_root_path: str = "",
    ):
        self.app = app
        self.trust_all = trust_all
        self.trust_proto = trust_proto
        self.trust_host = trust_host
        self.trust_prefix = trust_prefix
        self.default_root_path = default_root_path.rstrip("/") if default_root_path else ""

        self.trusted_networks: List[Union[ipaddress.IPv4Network, ipaddress.IPv6Network]] = []
        self.trusted_names = set()
        raw_proxies = []
        if isinstance(trusted_proxies, str):
            raw_proxies = [p.strip() for p in trusted_proxies.replace(";", ",").split(",") if p.strip()]
        elif isinstance(trusted_proxies, (list, tuple, set)):
            raw_proxies = [str(p).strip() for p in trusted_proxies if str(p).strip()]

        for item in raw_proxies:
            if item in ("*", "all", "ANY"):
                self.trust_all = True
                break
            if item.lower() in ("testclient", "localhost"):
                self.trusted_names.add(item.lower())
                continue
            try:
                if "/" not in item:
                    ip = ipaddress.ip_address(item)
                    net = ipaddress.ip_network(f"{item}/{32 if ip.version == 4 else 128}", strict=False)
                else:
                    net = ipaddress.ip_network(item, strict=False)
                self.trusted_networks.append(net)
            except ValueError as e:
                _log.warning("Invalid trusted proxy IP/CIDR %r: %s", item, e)

    def is_ip_trusted(self, ip_str: Optional[str]) -> bool:
        if not ip_str:
            return False
        if self.trust_all:
            return True
        if ip_str.lower() in self.trusted_names:
            return True
        check_ip = "127.0.0.1" if ip_str.lower() in ("testclient", "localhost") else ip_str
        try:
            ip = ipaddress.ip_address(check_ip)
            return any(ip in net for net in self.trusted_networks)
        except ValueError:
            return False

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        header_map = {}
        for k, v in scope.get("headers", []):
            try:
                header_map[k.decode("latin1").lower()] = v.decode("latin1").strip()
            except Exception:
                pass

        client = scope.get("client")
        connecting_ip = client[0] if client else None
        connecting_port = client[1] if client else 0

        is_trusted = self.is_ip_trusted(connecting_ip)

        forwarded_for_raw = header_map.get("x-forwarded-for")
        forwarded_proto_raw = header_map.get("x-forwarded-proto")
        forwarded_host_raw = header_map.get("x-forwarded-host")
        forwarded_prefix_raw = header_map.get("x-forwarded-prefix")

        effective_client_ip = connecting_ip or "127.0.0.1"
        effective_scheme = scope.get("scheme", "http")
        effective_host = header_map.get("host", "")

        # Apply default root path if configured and not already set
        if self.default_root_path and not scope.get("root_path"):
            scope["root_path"] = self.default_root_path

        if is_trusted:
            if forwarded_for_raw:
                # X-Forwarded-For: client, proxy1, proxy2
                hops = [h.strip() for h in forwarded_for_raw.split(",") if h.strip()]
                if hops:
                    effective_client_ip = hops[0]
                    scope["client"] = (effective_client_ip, connecting_port)

            if self.trust_proto and forwarded_proto_raw:
                proto = forwarded_proto_raw.split(",")[0].strip().lower()
                if proto in ("http", "https"):
                    effective_scheme = proto
                    scope["scheme"] = proto

            if self.trust_host and forwarded_host_raw:
                host_val = forwarded_host_raw.split(",")[0].strip()
                effective_host = host_val
                new_headers = [(k, v) for k, v in scope.get("headers", []) if k.lower() != b"host"]
                new_headers.append((b"host", host_val.encode("latin1")))
                scope["headers"] = new_headers

            if self.trust_prefix and forwarded_prefix_raw:
                prefix = forwarded_prefix_raw.split(",")[0].strip().rstrip("/")
                if prefix:
                    scope["root_path"] = prefix

        state = scope.setdefault("state", {})
        state["proxy"] = {
            "connecting_ip": connecting_ip,
            "is_trusted": is_trusted,
            "effective_client_ip": effective_client_ip,
            "effective_scheme": effective_scheme,
            "effective_host": effective_host,
            "root_path": scope.get("root_path", ""),
            "x_forwarded_for": forwarded_for_raw,
            "x_forwarded_proto": forwarded_proto_raw,
            "x_forwarded_host": forwarded_host_raw,
            "x_forwarded_prefix": forwarded_prefix_raw,
        }

        return await self.app(scope, receive, send)
