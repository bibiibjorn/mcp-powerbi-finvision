"""The PAC file the gateway serves (``GET /proxy.pac``, spec §8.2).

``build_pac(previous, port, masked_any)`` returns a ``FindProxyForURL`` that

* sends the routed AI hosts (``claude.ai``, ``chatgpt.com``, ... and every subdomain) to the gateway's
  front end, ``PROXY 127.0.0.1:<port>``, with ``; DIRECT`` behind it only while no chat is masked: a
  masked chat must never fall back to a direct connection that would bypass the masking (spec §8.2);
* answers everything else exactly as the PREVIOUS settings did (R7: nobody else's traffic changes):
  ``DIRECT``; a static proxy (``ProxyServer`` incl. the per-protocol syntax, with its ``ProxyOverride``
  bypass list incl. ``<local>``); or a previous PAC script, embedded with its ``FindProxyForURL``
  renamed and called for those hosts.

``classify(settings)`` reads the previous Internet Settings into a ``Previous`` and REFUSES
(``RoutingRefused(code)``) what it cannot reproduce: WPAD auto-detection (the decision comes from a
server we cannot see) unless the caller knows it is direct, settings locked by policy, a previous PAC that
cannot be fetched or is not http / https / file, a proxy string it cannot read. ``prepare_previous``
also fetches a previous PAC ONCE (timeout, size limit, no proxy) and caches it in
``<home>/routing/previous.pac``.

The generated text uses only ``isPlainHostName`` and ``shExpMatch`` (and ``dnsDomainIs`` is not needed),
so any PAC engine runs it. Every string put in the script is a JSON string literal (never concatenated).
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from client_gateway.routing.ca import ROUTED_DOMAINS, ROUTING_DIR
from client_gateway.routing.winproxy import ProxySettings

PREVIOUS_PAC_FILE = "previous.pac"
FETCH_TIMEOUT_S = 5.0
FETCH_MAX_BYTES = 1_048_576
PAC_CONTENT_TYPE = "application/x-ns-proxy-autoconfig"
RENAMED = "__fv_previous_FindProxyForURL"
_FIND = re.compile(r"\bFindProxyForURL\b")
_SCHEMES = ("http", "https", "ftp")


class RoutingRefused(Exception):
    """Routing cannot be enabled. The message is a fixed code: ``locked``, ``autodetect``,
    ``pac_unfetchable``, ``pac_scheme``, ``proxy_unparseable``."""


@dataclass(frozen=True)
class StaticProxy:
    """A static proxy: ``entries`` maps ``"*"`` (one proxy for every protocol), ``http``, ``https``,
    ``ftp`` or ``socks`` to ``host:port``; ``bypass`` is the ``ProxyOverride`` list."""

    entries: dict[str, str]
    bypass: tuple[str, ...] = ()

    def for_scheme(self, scheme: str) -> str | None:
        """The PAC answer for a URL scheme (``PROXY h:p`` / ``SOCKS h:p`` / None = direct). WebSocket
        schemes follow http / https, as in WinINet."""
        scheme = {"ws": "http", "wss": "https"}.get(scheme, scheme)
        if scheme in self.entries:
            return f"PROXY {self.entries[scheme]}"
        if "*" in self.entries:
            return f"PROXY {self.entries['*']}"
        if "socks" in self.entries:
            return f"SOCKS {self.entries['socks']}"
        return None


@dataclass(frozen=True)
class Previous:
    """The decision that was in force before routing: ``direct``, ``static`` or ``pac``."""

    kind: str
    static: StaticProxy | None = None
    pac_url: str | None = None
    script: str | None = field(default=None, repr=False)


DIRECT = Previous("direct")


# ------------------------------------------------------------------------------------ parsing
_HOST_PORT = re.compile(r"^[A-Za-z0-9._\-\[\]:]+$")


def parse_proxy_server(spec: str) -> dict[str, str]:
    """``"h:p"`` -> ``{"*": "h:p"}``; ``"http=a:80;https=b:443;socks=c:1080"`` -> per protocol.
    A ``scheme://`` prefix on an entry is dropped. ``RoutingRefused("proxy_unparseable")`` for
    anything else."""
    out: dict[str, str] = {}
    for part in re.split(r"[;\s]+", (spec or "").strip()):
        if not part:
            continue
        key, sep, value = part.partition("=")
        if not sep:
            key, value = "*", part
        key = key.lower()
        value = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", value).rstrip("/")
        if key not in ("*", *_SCHEMES, "socks") or not value or not _HOST_PORT.match(value):
            raise RoutingRefused("proxy_unparseable")
        out[key] = value
    if not out:
        raise RoutingRefused("proxy_unparseable")
    return out


def parse_override(spec: str | None) -> tuple[str, ...]:
    """The ``ProxyOverride`` entries (``<local>``, ``*.corp.local``, ``10.*``), separated by ``;``."""
    items = [i.strip() for i in re.split(r"[;,]", spec or "") if i.strip()]
    return tuple(re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", i).lower() for i in items)


def classify(settings: ProxySettings, *, autodetect_is_direct: bool = False) -> Previous:
    """The previous decision as a ``Previous`` (no network: a PAC's script is fetched by
    ``prepare_previous``). ``autodetect_is_direct``: the caller verified that WPAD auto-detection
    yields nothing here (setup probes it), so ``AutoDetect=1`` does not refuse routing."""
    if settings.locked:
        raise RoutingRefused("locked")
    detect = settings.auto_detect == 1
    if detect and not autodetect_is_direct:
        raise RoutingRefused("autodetect")  # WPAD decides: a server we cannot see
    if settings.auto_config_url:
        return Previous("pac", pac_url=settings.auto_config_url)
    if settings.proxy_enable == 1 and settings.proxy_server:
        entries = parse_proxy_server(settings.proxy_server)
        return Previous(
            "static", static=StaticProxy(entries, parse_override(settings.proxy_override))
        )
    return DIRECT


def fetch_pac_script(url: str, timeout: float = FETCH_TIMEOUT_S) -> str:
    """The text of a PAC (http / https / file), without any proxy, within ``timeout`` and
    ``FETCH_MAX_BYTES``. ``RoutingRefused`` for another scheme or any failure."""
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ("http", "https", "file"):
        raise RoutingRefused("pac_scheme")
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as response:  # noqa: S310 - scheme checked above
            data = response.read(FETCH_MAX_BYTES + 1)
    except Exception:  # noqa: BLE001 - fixed code: the URL / error text may name a company host
        raise RoutingRefused("pac_unfetchable") from None
    if not data or len(data) > FETCH_MAX_BYTES:
        raise RoutingRefused("pac_unfetchable")
    text = data.decode("utf-8", "replace")
    if not _FIND.search(text):
        raise RoutingRefused("pac_unfetchable")  # not a PAC script
    return text


def prepare_previous(
    settings: ProxySettings,
    home: Path,
    *,
    fetch: Callable[[str], str] = fetch_pac_script,
    autodetect_is_direct: bool = False,
) -> Previous:
    """``classify`` plus the one-time fetch of a previous PAC (cached in ``previous.pac``)."""
    previous = classify(settings, autodetect_is_direct=autodetect_is_direct)
    if previous.kind != "pac" or previous.pac_url is None:
        return previous
    script = fetch(previous.pac_url)
    if not _FIND.search(script):
        raise RoutingRefused("pac_unfetchable")
    cache = Path(home) / ROUTING_DIR / PREVIOUS_PAC_FILE
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_name(cache.name + ".tmp")
    tmp.write_bytes(script.encode("utf-8"))
    os.replace(tmp, cache)
    return Previous("pac", pac_url=previous.pac_url, script=script)


# ------------------------------------------------------------------------------------ building
def _js(value: str) -> str:
    """A JavaScript string literal (JSON is one)."""
    return json.dumps(value)


def _routed_test(domains: tuple[str, ...]) -> str:
    parts = []
    for domain in domains:
        parts.append(f"shExpMatch(host, {_js(domain)})")
        parts.append(f"shExpMatch(host, {_js('*.' + domain)})")
    return " ||\n      ".join(parts)


def _static_function(static: StaticProxy) -> str:
    lines = ["function __fv_previous(url, host) {"]
    for item in static.bypass:
        if item == "<local>":
            lines.append('  if (isPlainHostName(host)) return "DIRECT";')
        else:
            lines.append(f'  if (shExpMatch(host, {_js(item)})) return "DIRECT";')
    lines.append('  var scheme = url.substring(0, url.indexOf(":")).toLowerCase();')
    lines.append('  if (scheme == "ws") scheme = "http";')
    lines.append('  if (scheme == "wss") scheme = "https";')
    for scheme in _SCHEMES:
        if scheme in static.entries:
            lines.append(
                f'  if (scheme == "{scheme}") return {_js("PROXY " + static.entries[scheme])};'
            )
    fallback = (
        f"PROXY {static.entries['*']}"
        if "*" in static.entries
        else f"SOCKS {static.entries['socks']}" if "socks" in static.entries else "DIRECT"
    )
    lines.append(f"  return {_js(fallback)};")
    lines.append("}")
    return "\n".join(lines)


def build_pac(
    previous: Previous,
    port: int,
    masked_any: bool,
    domains: tuple[str, ...] = ROUTED_DOMAINS,
) -> str:
    """The PAC text (see the module docstring). ``masked_any``: some chat is masked right now, so the
    routed hosts get NO ``DIRECT`` fallback."""
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        raise ValueError("bad_port")
    answer = f"PROXY 127.0.0.1:{port}" + ("" if masked_any else "; DIRECT")
    out = [
        "// Finvision masking gateway - generated, do not edit. Only the AI hosts below go through the",
        "// local gateway; every other host is answered as before this file was set.",
        "function FindProxyForURL(url, host) {",
        "  host = host.toLowerCase();",
        f"  if ({_routed_test(domains)}) {{",
        f"    return {_js(answer)};",
        "  }",
        "  return __fv_previous(url, host);",
        "}",
        "",
    ]
    if previous.kind == "direct":
        out += ["function __fv_previous(url, host) {", '  return "DIRECT";', "}"]
    elif previous.kind == "static" and previous.static is not None:
        out.append(_static_function(previous.static))
    elif previous.kind == "pac" and previous.script is not None:
        out += [
            "function __fv_previous(url, host) {",
            f"  return {RENAMED}(url, host);",
            "}",
            "",
            "// ---- the previous PAC, embedded (its FindProxyForURL renamed) ----",
            _FIND.sub(RENAMED, previous.script),
        ]
    else:
        raise ValueError("bad_previous")
    return "\n".join(out) + "\n"


class PacProvider:
    """What ``GET /proxy.pac`` serves: the PAC for the current ``masked_any`` (rebuilt when it flips)."""

    def __init__(
        self,
        previous: Previous,
        port: int,
        masked_any: Callable[[], bool],
        domains: tuple[str, ...] = ROUTED_DOMAINS,
    ) -> None:
        self.previous = previous
        self.port = port
        self.domains = domains
        self._masked_any = masked_any
        self._cache: dict[bool, str] = {}

    def text(self) -> str:
        masked = bool(self._masked_any())
        if masked not in self._cache:
            self._cache[masked] = build_pac(self.previous, self.port, masked, self.domains)
        return self._cache[masked]
