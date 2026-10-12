"""Parse pbir schema JSON + harvest all in-scope types to cache/."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tools.visual_catalog.types import IN_SCOPE_TYPES, is_in_scope


class SchemaParseError(ValueError):
    """Raised when pbir schema JSON is missing expected keys."""


@dataclass(frozen=True)
class PropertySpec:
    name: str
    type: str
    description: str
    default: Optional[Any] = None
    allowed_values: Optional[List[Any]] = None
    selector_aware: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContainerSurface:
    visual_type: str
    container: str
    properties: List[PropertySpec]


@dataclass(frozen=True)
class ContainerListingEntry:
    name: str
    property_count: int


@dataclass(frozen=True)
class ContainerListing:
    visual_type: str
    containers: List[ContainerListingEntry]


@dataclass(frozen=True)
class RoleSpec:
    name: str
    required: bool
    multiple: bool
    accepts: List[str]


@dataclass(frozen=True)
class RolesSurface:
    visual_type: str
    roles: Dict[str, RoleSpec]


def parse_describe_json(raw: Dict[str, Any]) -> ContainerSurface:
    if "properties" not in raw or "type" not in raw or "container" not in raw:
        raise SchemaParseError(
            f"Expected keys {{type, container, properties}} in describe JSON, got {sorted(raw.keys())}"
        )
    props = [
        PropertySpec(
            name=name,
            type=spec.get("type", "unknown"),
            description=spec.get("description", ""),
            default=spec.get("default"),
            allowed_values=spec.get("enum") or spec.get("allowedValues"),
            selector_aware=bool(spec.get("selectorAware", False)),
            raw=spec,
        )
        for name, spec in raw["properties"].items()
    ]
    return ContainerSurface(raw["type"], raw["container"], props)


def parse_containers_json(raw: Dict[str, Any]) -> ContainerListing:
    vt = raw.get("visual_type") or raw.get("type") or ""
    body = raw.get("containers")
    if isinstance(body, list):
        entries = []
        for c in body:
            if isinstance(c, dict):
                entries.append(ContainerListingEntry(c["name"], int(c.get("property_count", 0))))
            else:
                # Real pbir output: containers is a list of strings
                entries.append(ContainerListingEntry(str(c), 0))
    elif isinstance(body, dict):
        entries = [
            ContainerListingEntry(n, int(v.get("property_count", 0))) for n, v in body.items()
        ]
    else:
        raise SchemaParseError(f"Unrecognized containers JSON shape: {sorted(raw.keys())}")
    return ContainerListing(vt, entries)


def parse_roles_json(raw: Dict[str, Any]) -> RolesSurface:
    if "roles" not in raw:
        raise SchemaParseError(f"Expected 'roles' key, got {sorted(raw.keys())}")
    vt = raw.get("visualType") or raw.get("visual_type") or raw.get("type") or ""
    roles = {
        name: RoleSpec(
            name=name,
            required=bool(spec.get("required", False)),
            multiple=bool(spec.get("multiple", False)),
            accepts=list(spec.get("accepts", [])),
        )
        for name, spec in raw["roles"].items()
    }
    return RolesSurface(vt, roles)


def _run_pbir(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def harvest_schema(
    cache_root: Path,
    only_types: Optional[Iterable[str]] = None,
) -> List[str]:
    """Shell out to `pbir schema` for every in-scope type; write JSON to cache."""
    warnings: List[str] = []
    types = list(only_types) if only_types else list(IN_SCOPE_TYPES)

    for t in types:
        if not is_in_scope(t):
            continue

        roles = _run_pbir(["pbir", "schema", "roles", t, "--json"])
        if roles.returncode == 0:
            try:
                _write_json(cache_root / t / "_roles.json", json.loads(roles.stdout))
            except json.JSONDecodeError:
                # pbir prints plain text (e.g. "textbox has no data roles") for
                # types that have no data roles. Write a synthetic empty-roles
                # payload so the per-type file is still generated.
                _write_json(
                    cache_root / t / "_roles.json",
                    {"visualType": t, "roles": {}},
                )
        else:
            warnings.append(f"roles failed for {t}: {roles.stderr.strip()}")

        containers = _run_pbir(["pbir", "schema", "containers", t, "--json"])
        if containers.returncode != 0:
            warnings.append(f"containers failed for {t}: {containers.stderr.strip()}")
            continue
        try:
            listing_raw = json.loads(containers.stdout)
        except json.JSONDecodeError as e:
            warnings.append(f"containers JSON parse failed for {t}: {e}")
            continue
        _write_json(cache_root / t / "_containers.json", listing_raw)

        body = listing_raw.get("containers")
        if isinstance(body, list):
            names = []
            for c in body:
                if isinstance(c, dict):
                    names.append(c["name"])
                else:
                    names.append(str(c))
        elif isinstance(body, dict):
            names = list(body.keys())
        else:
            names = []

        for cname in names:
            desc = _run_pbir(["pbir", "schema", "describe", f"{t}.{cname}", "--json"])
            if desc.returncode != 0:
                warnings.append(f"describe failed for {t}.{cname}: {desc.stderr.strip()}")
                continue
            try:
                _write_json(cache_root / t / f"{cname}.json", json.loads(desc.stdout))
            except json.JSONDecodeError as e:
                warnings.append(f"describe JSON parse failed for {t}.{cname}: {e}")

    return warnings
