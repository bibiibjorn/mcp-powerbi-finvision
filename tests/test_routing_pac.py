"""The PAC file (phase 3b.3): one test per previous-proxy case. The generated text is asserted exactly where
it matters and EVALUATED by a real PAC engine (node's ``vm`` with the standard PAC helpers) so the
routing decisions are checked, not only the text. Node is the only tool; no browser, no system setting.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from client_gateway.routing import pac as pac_mod
from client_gateway.routing.ca import ROUTED_DOMAINS
from client_gateway.routing.pac import (
    DIRECT,
    PacProvider,
    Previous,
    RoutingRefused,
    StaticProxy,
    build_pac,
    classify,
    fetch_pac_script,
    parse_override,
    parse_proxy_server,
    prepare_previous,
)
from client_gateway.routing.winproxy import MemoryInternetSettings

PORT = 18890
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is needed to evaluate the PAC text")
HARNESS = textwrap.dedent("""
    const fs = require('fs'), vm = require('vm');
    const [pacPath, casesPath] = process.argv.slice(2);
    const sandbox = {
      isPlainHostName: h => h.indexOf('.') < 0,
      shExpMatch: (s, p) => new RegExp('^' + p.replace(/[.+^${}()|[\\]\\\\]/g, '\\\\$&')
          .replace(/\\*/g, '.*').replace(/\\?/g, '.') + '$', 'i').test(s),
      dnsDomainIs: (h, d) => h.toLowerCase().endsWith(d.toLowerCase()),
      dnsResolve: () => '127.0.0.1', myIpAddress: () => '127.0.0.1',
    };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(pacPath, 'utf8'), sandbox);
    const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));
    console.log(JSON.stringify(cases.map(u => {
      const host = new URL(u).hostname;
      return sandbox.FindProxyForURL(u, host);
    })));
    """)


def evaluate(tmp_path: Path, pac: str, urls: list[str]) -> list[str]:
    (tmp_path / "x.pac").write_text(pac, encoding="utf-8")
    (tmp_path / "cases.json").write_text(json.dumps(urls), encoding="utf-8")
    (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # R10: no console window
    run = subprocess.run(
        [NODE, str(tmp_path / "harness.js"), str(tmp_path / "x.pac"), str(tmp_path / "cases.json")],
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
    )
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert isinstance(out, list) and len(out) == len(urls)
    return out  # type: ignore[no-any-return]


ROUTED_URLS = [
    "https://chatgpt.com/c/1",
    "https://api.openai.com/v1/x",
    "https://claude.ai/",
    "https://api.individual.githubcopilot.com/chat",
    "https://files.oaiusercontent.com/x",
    "https://CHATGPT.COM/",
]
OTHER_URLS = [
    "https://example.com/",
    "https://notchatgpt.com/",
    "https://chatgpt.com.evil.example/",
]


# ---------------------------------------------------------------------------------- the routed part
def test_the_routed_block_is_exact_and_the_fallback_follows_the_masked_flag():
    unmasked = build_pac(DIRECT, PORT, masked_any=False)
    masked = build_pac(DIRECT, PORT, masked_any=True)
    for domain in ROUTED_DOMAINS:
        assert f'shExpMatch(host, "{domain}")' in unmasked
        assert f'shExpMatch(host, "*.{domain}")' in unmasked
    assert 'return "PROXY 127.0.0.1:18890; DIRECT";' in unmasked
    assert 'return "PROXY 127.0.0.1:18890";' in masked and "; DIRECT" not in masked.split("}")[0]
    assert unmasked.count("function FindProxyForURL") == 1


@needs_node
@pytest.mark.parametrize("masked", [False, True])
def test_routed_hosts_go_to_the_gateway_and_only_they_do(tmp_path, masked):
    pac = build_pac(DIRECT, PORT, masked)
    routed = evaluate(tmp_path, pac, ROUTED_URLS)
    other = evaluate(tmp_path, pac, OTHER_URLS)
    expected = "PROXY 127.0.0.1:18890" + ("" if masked else "; DIRECT")
    assert routed == [expected] * len(ROUTED_URLS)
    assert other == ["DIRECT"] * len(OTHER_URLS)


def test_a_bad_port_is_refused():
    for port in (0, -1, 70000, True, "8890"):
        with pytest.raises(ValueError, match="bad_port"):
            build_pac(DIRECT, port, False)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="bad_previous"):
        build_pac(Previous("pac"), PORT, False)  # a PAC without its script cannot be embedded


# ---------------------------------------------------------------------------------- previous = direct
@needs_node
def test_previous_direct_everything_else_stays_direct(tmp_path):
    settings = MemoryInternetSettings.with_values().read()
    previous = classify(settings)
    assert previous == DIRECT
    assert evaluate(tmp_path, build_pac(previous, PORT, False), OTHER_URLS) == ["DIRECT"] * 3


def test_a_disabled_static_proxy_is_direct():
    settings = MemoryInternetSettings.with_values(enable=0, server="p:1", detect=0).read()
    assert classify(settings) == DIRECT


# ---------------------------------------------------------------------------------- previous = static
def static_previous(**kw) -> Previous:
    previous = classify(MemoryInternetSettings.with_values(enable=1, detect=0, **kw).read())
    assert previous.kind == "static"
    return previous


@needs_node
def test_previous_static_proxy_with_a_bypass_list(tmp_path):
    previous = static_previous(server="proxy.corp:8080", override="<local>;*.corp.local;10.*")
    pac = build_pac(previous, PORT, False)
    urls = [
        "https://example.com/",
        "http://example.com/",
        "wss://example.com/s",
        "http://intranet/x",  # <local>: no dot in the host name
        "https://git.corp.local/",
        "https://10.1.2.3/",
        "https://chatgpt.com/",  # routed: ours wins over the previous proxy
    ]
    assert evaluate(tmp_path, pac, urls) == [
        "PROXY proxy.corp:8080",
        "PROXY proxy.corp:8080",
        "PROXY proxy.corp:8080",
        "DIRECT",
        "DIRECT",
        "DIRECT",
        "PROXY 127.0.0.1:18890; DIRECT",
    ]


@needs_node
def test_previous_per_protocol_static_proxy(tmp_path):
    previous = static_previous(server="http=h1:80;https=h2:443;ftp=h3:21")
    urls = ["http://a.example/", "https://a.example/", "ws://a.example/", "wss://a.example/"]
    assert evaluate(tmp_path, build_pac(previous, PORT, True), urls) == [
        "PROXY h1:80",
        "PROXY h2:443",
        "PROXY h1:80",
        "PROXY h2:443",
    ]
    only_http = static_previous(server="http=h1:80")
    assert evaluate(tmp_path, build_pac(only_http, PORT, True), urls) == [
        "PROXY h1:80",
        "DIRECT",
        "PROXY h1:80",
        "DIRECT",
    ]
    socks = static_previous(server="socks=s:1080")
    assert evaluate(tmp_path, build_pac(socks, PORT, True), urls) == ["SOCKS s:1080"] * 4


def test_the_python_model_of_a_static_proxy_agrees_with_the_generated_answers():
    static = StaticProxy({"http": "h1:80", "https": "h2:443"})
    assert static.for_scheme("http") == "PROXY h1:80"
    assert static.for_scheme("wss") == "PROXY h2:443" and static.for_scheme("ftp") is None
    assert StaticProxy({"*": "p:1"}).for_scheme("ftp") == "PROXY p:1"
    assert StaticProxy({"socks": "s:2"}).for_scheme("http") == "SOCKS s:2"


def test_proxy_server_and_override_parsing():
    assert parse_proxy_server("proxy.corp:8080") == {"*": "proxy.corp:8080"}
    assert parse_proxy_server("http://p:1/") == {"*": "p:1"}
    assert parse_proxy_server("HTTP=a:1; https=https://b:2 ;socks=c:3") == {
        "http": "a:1",
        "https": "b:2",
        "socks": "c:3",
    }
    for bad in ("", "  ", "gopher=a:1", "http=", "a b c:1;;x=y", 'p:1"; evil("'):
        with pytest.raises(RoutingRefused, match="proxy_unparseable"):
            parse_proxy_server(bad)
    assert parse_override("<local>; *.Corp.Local ;;https://x.example,10.*") == (
        "<local>",
        "*.corp.local",
        "x.example",
        "10.*",
    )
    assert parse_override(None) == () and parse_override("") == ()


def test_a_hostile_bypass_entry_cannot_break_out_of_its_string():
    previous = Previous("static", static=StaticProxy({"*": "p:1"}, ('x"); evil("',)))
    pac = build_pac(previous, PORT, False)
    assert 'shExpMatch(host, "x\\"); evil(\\"")' in pac and "evil(" not in pac.replace(
        'evil(\\"', ""
    )


# ---------------------------------------------------------------------------------- previous = PAC
OLD_PAC = textwrap.dedent("""
    function FindProxyForURL(url, host) {
      if (dnsDomainIs(host, ".corp")) return "DIRECT";
      return "PROXY corp-proxy:3128";
    }
    """)


@needs_node
def test_previous_pac_is_embedded_renamed_and_called_for_everything_else(tmp_path):
    previous = Previous("pac", pac_url="http://wpad.corp/p.pac", script=OLD_PAC)
    pac = build_pac(previous, PORT, False)
    assert pac.count("function FindProxyForURL") == 1  # ours; the old one is renamed
    assert "function __fv_previous_FindProxyForURL" in pac
    urls = [
        "https://example.com/",
        "https://intranet.corp/",
        "https://chatgpt.com/",
        "https://claude.ai/",
    ]
    assert evaluate(tmp_path, pac, urls) == [
        "PROXY corp-proxy:3128",
        "DIRECT",
        "PROXY 127.0.0.1:18890; DIRECT",
        "PROXY 127.0.0.1:18890; DIRECT",
    ]


def test_every_use_of_the_old_function_name_is_renamed():
    script = (
        "function FindProxyForURL(u,h){return FindProxyForURL2(u,h);}\nvar x = FindProxyForURL;"
    )
    pac = build_pac(Previous("pac", script=script), PORT, False)
    assert "var x = __fv_previous_FindProxyForURL;" in pac
    assert "FindProxyForURL2" in pac  # a different word is left alone


# ---------------------------------------------------------------------------------- classify
@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"detect": 1}, "autodetect"),  # WPAD only
        ({"detect": 1, "enable": 1, "server": "p:1"}, "autodetect"),
        ({"detect": 1, "pac": "http://x/p.pac"}, "autodetect"),
        ({"locked": ("proxy_settings_per_machine",)}, "locked"),
        ({"enable": 1, "server": 'p:1"; x("'}, "proxy_unparseable"),
    ],
)
def test_what_cannot_be_reproduced_is_refused_with_a_reason(kw, code):
    with pytest.raises(RoutingRefused, match=code):
        classify(MemoryInternetSettings.with_values(**kw).read())


def test_autodetect_that_the_caller_proved_direct_is_allowed():
    assert (
        classify(MemoryInternetSettings.with_values(detect=1).read(), autodetect_is_direct=True)
        == DIRECT
    )
    previous = classify(
        MemoryInternetSettings.with_values(detect=1, enable=1, server="p:1").read(),
        autodetect_is_direct=True,
    )
    assert previous.kind == "static"


def test_a_pac_url_is_a_pac_previous_without_the_network():
    previous = classify(
        MemoryInternetSettings.with_values(pac="http://wpad.corp/p.pac", detect=0).read()
    )
    assert previous == Previous("pac", pac_url="http://wpad.corp/p.pac")


# ---------------------------------------------------------------------------------- fetching
def test_prepare_previous_fetches_once_and_caches_the_script(tmp_path):
    settings = MemoryInternetSettings.with_values(pac="http://wpad.corp/p.pac", detect=0).read()
    asked = []

    def fetch(url):
        asked.append(url)
        return OLD_PAC

    previous = prepare_previous(settings, tmp_path, fetch=fetch)
    assert asked == ["http://wpad.corp/p.pac"] and previous.script == OLD_PAC
    assert (tmp_path / "routing" / "previous.pac").read_bytes() == OLD_PAC.encode("utf-8")


def test_a_previous_pac_that_cannot_be_fetched_refuses_routing_and_caches_nothing(tmp_path):
    settings = MemoryInternetSettings.with_values(pac="http://wpad.corp/p.pac", detect=0).read()

    def boom(url):
        raise RoutingRefused("pac_unfetchable")

    with pytest.raises(RoutingRefused, match="pac_unfetchable"):
        prepare_previous(settings, tmp_path, fetch=boom)
    with pytest.raises(RoutingRefused, match="pac_unfetchable"):
        prepare_previous(settings, tmp_path, fetch=lambda url: "<html>not a pac</html>")
    assert not (tmp_path / "routing" / "previous.pac").exists()


def test_non_pac_previous_cases_never_fetch(tmp_path):
    def never(url):
        raise AssertionError("fetched")

    assert (
        prepare_previous(MemoryInternetSettings.with_values().read(), tmp_path, fetch=never)
        == DIRECT
    )


def test_fetching_a_pac_from_a_file_and_the_limits(tmp_path, monkeypatch):
    script = tmp_path / "corp.pac"
    script.write_bytes(OLD_PAC.encode("utf-8"))
    assert fetch_pac_script(script.as_uri()) == OLD_PAC
    for url in ("ftp://x/p.pac", "gopher://x/p", "javascript:alert(1)", ""):
        with pytest.raises(RoutingRefused, match="pac_scheme"):
            fetch_pac_script(url)
    missing = (tmp_path / "missing.pac").as_uri()
    with pytest.raises(RoutingRefused, match="pac_unfetchable"):
        fetch_pac_script(missing)
    not_pac = tmp_path / "x.pac"
    not_pac.write_text("hello", encoding="utf-8")
    with pytest.raises(RoutingRefused, match="pac_unfetchable"):
        fetch_pac_script(not_pac.as_uri())
    monkeypatch.setattr(pac_mod, "FETCH_MAX_BYTES", 10)
    with pytest.raises(RoutingRefused, match="pac_unfetchable"):
        fetch_pac_script(script.as_uri())


def test_the_fetch_goes_without_any_proxy(monkeypatch, tmp_path):
    seen = {}
    real = pac_mod.urllib.request.build_opener

    def spy(*handlers):
        seen["proxy_handler"] = [
            h for h in handlers if isinstance(h, pac_mod.urllib.request.ProxyHandler)
        ]
        return real(*handlers)

    monkeypatch.setattr(pac_mod.urllib.request, "build_opener", spy)
    script = tmp_path / "c.pac"
    script.write_bytes(OLD_PAC.encode("utf-8"))
    fetch_pac_script(script.as_uri())
    assert seen["proxy_handler"] and seen["proxy_handler"][0].proxies == {}


# ---------------------------------------------------------------------------------- the provider
def test_the_provider_rebuilds_when_the_masked_flag_flips():
    flag = [False]
    provider = PacProvider(DIRECT, PORT, lambda: flag[0])
    first = provider.text()
    assert "; DIRECT" in first.split("}")[0] and provider.text() == first
    flag[0] = True
    second = provider.text()
    assert second != first and "; DIRECT" not in second.split("}")[0]
    flag[0] = False
    assert provider.text() == first
