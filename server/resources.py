"""
MCP Resources Management

Static / dynamic resources, URI-templated resources and exported files, exposed
to MCP clients. Fixes over the pre-2026-09-22 version (A-01 / L-14):

* URIs are normalised with ``str()`` — the SDK passes a pydantic ``AnyUrl``,
  which never equals the ``str`` dict key, so every ``resources/read`` failed.
* :meth:`ResourceManager.aread` returns ``ReadResourceContents`` with the
  DECLARED mime type (a bare ``str`` is deprecated and forced ``text/plain``).
* Providers may be ``async`` (e.g. the roots resource awaits ``list_roots`` on
  the loop instead of blocking it).
* URI templates (``powerbi://docs/tools/{tool}`` …) are supported and listed
  via ``resources/templates/list``.
* F-29: live templates (``powerbi://model/measure/{table}/{name}`` ...) register with
  ``lenient=True`` (parameter values are lookup keys, never paths, so measure / page names
  with ``:`` ``%`` ``[`` are allowed; control characters, backslashes and ``..`` are not)
  and ``offload=True`` (the read touches Desktop / disk, so ``aread`` runs it on a worker
  thread instead of blocking the event loop).
* Exports are registered by the response pipeline (spilled large results), so
  ``powerbi://export/latest`` is reachable.
"""

import gzip
import inspect
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from mcp.types import Resource, ResourceTemplate

logger = logging.getLogger(__name__)

_TEMPLATE_PARAM = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
#: Allowed characters for a template parameter value (no path separators).
_SAFE_PARAM = re.compile(r"^[A-Za-z0-9 _.()&+\-]+$")
#: ``lenient`` templates: any printable text without control chars / backslashes.
_LENIENT_PARAM = re.compile(r"^[^\x00-\x1f\x7f\\]{1,200}$")

_TEXT_SUFFIXES = {
    ".json": "application/json",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".tmdl": "text/plain",
    ".dax": "text/plain",
    ".html": "text/html",
}


@dataclass
class ResourceContent:
    """Content + mime type of one resource read (sync API)."""

    content: str
    mime_type: str


@dataclass
class _Template:
    uri_template: str
    name: str
    description: str
    mime_type: str
    resolver: Callable[..., Any]
    regex: "re.Pattern[str]"
    params: Tuple[str, ...]
    lenient: bool = False
    offload: bool = False


def _compile_template(uri_template: str) -> Tuple["re.Pattern[str]", Tuple[str, ...]]:
    params = tuple(_TEMPLATE_PARAM.findall(uri_template))
    pattern = ""
    pos = 0
    for m in _TEMPLATE_PARAM.finditer(uri_template):
        pattern += re.escape(uri_template[pos : m.start()])
        pattern += f"(?P<{m.group(1)}>[^/]+)"
        pos = m.end()
    pattern += re.escape(uri_template[pos:])
    return re.compile(f"^{pattern}$"), params


class ResourceManager:
    """Manages MCP resources. Thread-safe."""

    MAX_EXPORT_CACHE_ENTRIES = 50
    MAX_DYNAMIC_RESOURCES = 100

    def __init__(self):
        """Initialize resource manager"""
        self._export_cache: Dict[str, Dict] = {}  # uri -> metadata
        self._latest_export: Optional[str] = None
        self._dynamic_resources: Dict[str, Dict] = {}  # uri -> {provider, metadata}
        self._templates: Dict[str, _Template] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_export(self, file_path: str, metadata: Dict) -> str:
        """
        Register an exported file as an MCP resource

        Args:
            file_path: Path to the exported file
            metadata: Export metadata (format, size, statistics, etc.)

        Returns:
            Resource URI
        """
        file_name = Path(file_path).name
        uri = f"powerbi://export/{file_name}"

        with self._lock:
            # Evict oldest entries if cache is full
            while len(self._export_cache) >= self.MAX_EXPORT_CACHE_ENTRIES:
                oldest_key = next(iter(self._export_cache))
                del self._export_cache[oldest_key]

            self._export_cache[uri] = {
                "file_path": str(file_path),
                "metadata": dict(metadata or {}),
                "uri": uri,
            }
            self._latest_export = uri

        logger.debug(f"Registered MCP resource: {uri}")
        return uri

    def register_dynamic_resource(
        self,
        uri: str,
        name: str,
        description: str,
        provider: Callable[[], Any],
        mime_type: str = "text/markdown",
        offload: bool = False,
    ):
        """
        Register a dynamic resource that generates content on-demand.

        ``provider`` is a zero-arg callable returning ``str``, or an ``async``
        callable (awaited on the event loop by :meth:`aread`).
        """
        with self._lock:
            # Evict oldest entries if cache is full
            while len(self._dynamic_resources) >= self.MAX_DYNAMIC_RESOURCES:
                oldest_key = next(iter(self._dynamic_resources))
                del self._dynamic_resources[oldest_key]

            self._dynamic_resources[str(uri)] = {
                "name": name,
                "description": description,
                "provider": provider,
                "mime_type": mime_type,
                "offload": offload,
            }
        logger.debug(f"Registered dynamic MCP resource: {uri}")

    def register_template(
        self,
        uri_template: str,
        name: str,
        description: str,
        resolver: Callable[..., Any],
        mime_type: str = "text/markdown",
        lenient: bool = False,
        offload: bool = False,
    ) -> None:
        """Register a URI-templated resource. ``resolver(**params)`` returns the
        content (str) or raises ``LookupError`` when the instance doesn't exist."""
        regex, params = _compile_template(uri_template)
        with self._lock:
            self._templates[uri_template] = _Template(
                uri_template,
                name,
                description,
                mime_type,
                resolver,
                regex,
                params,
                lenient,
                offload,
            )

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_resources(self) -> List[Resource]:
        """List all available resources"""
        resources = []

        with self._lock:
            dynamic_snapshot = dict(self._dynamic_resources)
            export_snapshot = dict(self._export_cache)
            latest = self._latest_export

        for uri, data in dynamic_snapshot.items():
            resources.append(
                Resource(
                    uri=uri,
                    name=data["name"],
                    description=data["description"],
                    mimeType=data["mime_type"],
                )
            )

        if latest:
            resources.append(
                Resource(
                    uri="powerbi://export/latest",
                    name="Latest Export",
                    description="Most recently exported / spilled tool output",
                    mimeType=self._get_mime_type(
                        export_snapshot.get(latest, {}).get("file_path", "")
                    ),
                )
            )

        for uri, data in export_snapshot.items():
            metadata = data["metadata"]
            desc = metadata.get("description") or (
                f"Power BI export ({metadata.get('format', 'json')}, "
                f"{metadata.get('file_size_mb', 0)} MB)"
            )
            resources.append(
                Resource(
                    uri=uri,
                    name=Path(data["file_path"]).name,
                    description=desc,
                    mimeType=self._get_mime_type(data["file_path"]),
                )
            )

        return resources

    def list_templates(self) -> List[ResourceTemplate]:
        with self._lock:
            templates = list(self._templates.values())
        return [
            ResourceTemplate(
                uriTemplate=t.uri_template,
                name=t.name,
                description=t.description,
                mimeType=t.mime_type,
            )
            for t in templates
        ]

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def _resolve(self, uri: str) -> Tuple[str, Any, str]:
        """Return (kind, target, mime) — kind ∈ dynamic|template|export."""
        with self._lock:
            entry = self._dynamic_resources.get(uri)
            if entry is not None:
                return "dynamic", entry["provider"], entry["mime_type"]
            for t in self._templates.values():
                m = t.regex.match(uri)
                if m:
                    return "template", (t, m.groupdict()), t.mime_type
            if uri == "powerbi://export/latest":
                if not self._latest_export:
                    raise ValueError("No exports available")
                uri = self._latest_export
            if uri not in self._export_cache:
                raise ValueError(f"Resource not found: {uri}")
            file_path = self._export_cache[uri]["file_path"]
        return "export", file_path, self._get_mime_type(file_path)

    @staticmethod
    def _check_params(params: Dict[str, str], lenient: bool = False) -> None:
        from urllib.parse import unquote

        pattern = _LENIENT_PARAM if lenient else _SAFE_PARAM
        for key, value in list(params.items()):
            value = unquote(value)
            if not pattern.match(value) or ".." in value:
                raise ValueError(f"Invalid value for {key!r}: {value!r}")
            params[key] = value

    def read_resource(self, uri: Any) -> str:
        """Read resource content by URI (sync; async providers are not supported here)."""
        return self.read_resource_content(uri).content

    def read_resource_content(self, uri: Any) -> ResourceContent:
        """Sync read returning content + mime type."""
        uri = str(uri)
        kind, target, mime = self._resolve(uri)
        if kind == "dynamic":
            value = target()
            if inspect.isawaitable(value):
                close = getattr(value, "close", None)
                if callable(close):
                    close()
                raise ValueError(f"Resource {uri} is async; use aread()")
            return ResourceContent(str(value), mime)
        if kind == "template":
            tmpl, params = target
            self._check_params(params, tmpl.lenient)
            value = tmpl.resolver(**params)
            if inspect.isawaitable(value):
                raise ValueError(f"Resource {uri} is async; use aread()")
            return ResourceContent(str(value), mime)
        return ResourceContent(self._read_file(target), mime)

    async def aread(self, uri: Any) -> List[Any]:
        """Async read for the MCP ``resources/read`` handler.

        Returns ``[ReadResourceContents(content, mime_type)]`` so the declared
        mime type survives. Sync providers that may touch disk run inline (they
        are small file reads); async providers are awaited.
        """
        from mcp.server.lowlevel.helper_types import ReadResourceContents

        uri = str(uri)
        kind, target, mime = self._resolve(uri)
        if kind == "dynamic":
            with self._lock:
                offload = bool((self._dynamic_resources.get(uri) or {}).get("offload"))
            value = await self._run(target, offload)
        elif kind == "template":
            tmpl, params = target
            self._check_params(params, tmpl.lenient)
            value = await self._run(lambda: tmpl.resolver(**params), tmpl.offload)
        else:
            value = self._read_file(target)
        if inspect.isawaitable(value):
            value = await value
        return [ReadResourceContents(content=str(value), mime_type=mime)]

    @staticmethod
    async def _run(fn: Callable[[], Any], offload: bool) -> Any:
        """Call ``fn`` inline, or on a worker thread (``asyncio.to_thread`` copies the context)
        for resources that touch Desktop / disk."""
        if not offload:
            return fn()
        import asyncio

        return await asyncio.to_thread(fn)

    @staticmethod
    def _read_file(file_path: str) -> str:
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"Export file not found: {file_path}")
        if file_path.endswith(".json.gz"):
            with gzip.open(file_path, "rt", encoding="utf-8") as f:
                return f.read()
        if p.suffix.lower() in _TEXT_SUFFIXES:
            return p.read_text(encoding="utf-8")
        raise ValueError(f"Unsupported file format: {file_path}")

    def clear_cache(self):
        """Clear all cached resources"""
        with self._lock:
            self._export_cache.clear()
            self._latest_export = None
        logger.info("Resource cache cleared")

    def _get_mime_type(self, file_path: str) -> str:
        """Determine MIME type from file extension"""
        if file_path.endswith(".json.gz"):
            return "application/json"
        return _TEXT_SUFFIXES.get(Path(file_path).suffix.lower(), "application/octet-stream")


# ---------------------------------------------------------------------------
# Doc-section helpers (templated resources)
# ---------------------------------------------------------------------------


def markdown_section(text: str, heading_needle: str) -> Optional[str]:
    """Return the markdown section whose heading contains ``heading_needle``
    (up to the next heading of the same or higher level), or None."""
    lines = text.splitlines()
    start = None
    level = 0
    for i, line in enumerate(lines):
        if line.startswith("#"):
            hashes = len(line) - len(line.lstrip("#"))
            if start is None and heading_needle in line:
                start, level = i, hashes
                continue
            if start is not None and hashes <= level:
                return "\n".join(lines[start:i]).rstrip() + "\n"
    if start is None:
        return None
    return "\n".join(lines[start:]).rstrip() + "\n"


GUIDE_RELATIVE_PATH = "docs/GUIDE.md"
TOOL_REF_REGION_START = "<!-- tool-reference:start -->"
TOOL_REF_REGION_END = "<!-- tool-reference:end -->"


def tool_reference_region(guide_text: str) -> Optional[str]:
    """The tool-reference Part of ``docs/GUIDE.md`` (Part III): the text between the
    ``tool-reference:start`` / ``tool-reference:end`` markers, markers excluded, or None when
    either marker is missing. The resources ``powerbi://docs/tool-reference`` and
    ``powerbi://docs/tools/{tool}``, ``10_Show_User_Guide tool=<name>`` and the doc drift tests
    all read the guide through this one slice."""
    start = guide_text.find(TOOL_REF_REGION_START)
    end = guide_text.find(TOOL_REF_REGION_END)
    if start < 0 or end < start:
        return None
    return guide_text[start + len(TOOL_REF_REGION_START) : end].strip("\n") + "\n"


def doc_file_resolver(base_dir: Path, param: str) -> Callable[..., str]:
    """Resolver for ``{param}`` → ``base_dir/<value>.md`` (confined to base_dir)."""

    def _resolve(**params: str) -> str:
        name = params[param]
        target = (base_dir / f"{name}.md").resolve()
        base = base_dir.resolve()
        if base not in target.parents:
            raise ValueError("path escapes the document folder")
        if not target.exists():
            available = sorted(p.stem for p in base.glob("*.md"))[:80]
            raise LookupError(f"No document {name!r}. Available: {available}")
        return target.read_text(encoding="utf-8")

    return _resolve


# Global resource manager instance
_resource_manager = ResourceManager()


def get_resource_manager() -> ResourceManager:
    """Get the global resource manager instance"""
    return _resource_manager
