"""
PBIP Theme Engine - Manages theme files in PBIP report definitions.

The theme is referenced from ``report.json`` via
``themeCollection.customTheme.name`` and the actual theme JSON lives in
``{report_path}/StaticResources/RegisteredResources/{theme_name}``.
Here *report_path* is the ``.Report`` folder (parent of ``definition/``).

All public functions return ``Dict[str, Any]`` with ``success: bool`` plus
data, or ``error: str`` on failure.  No MCP awareness.
"""

import copy
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.pbip.authoring import safe_io
from core.pbip.authoring.formatting import unwrap_for_theme
from core.utilities.pbip_utils import load_json_file

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_report_path(report_path: Path) -> Path:
    """Ensure *report_path* points to the ``.Report`` folder.

    Accepts the .Report folder directly, or a ``definition/`` folder (goes up one).
    """
    report_path = Path(report_path)
    if report_path.name == "definition":
        report_path = report_path.parent
    return report_path


def _load_theme_ref(report_path: Path) -> Optional[Dict[str, Any]]:
    """Read ``report.json`` and return the ``themeCollection.customTheme`` dict."""
    definition_path = report_path / "definition"
    report_json_path = definition_path / "report.json"
    report_data = load_json_file(report_json_path)
    if report_data is None:
        return None
    return report_data.get("themeCollection", {}).get("customTheme")


def _resolve_theme_path(report_path: Path) -> Optional[Path]:
    """Resolve the filesystem path of the current theme JSON file."""
    theme_ref = _load_theme_ref(report_path)
    if not theme_ref:
        return None

    theme_name = theme_ref.get("name", "")
    theme_type = theme_ref.get("type", "RegisteredResources")

    if not theme_name:
        return None

    theme_path = report_path / "StaticResources" / theme_type / theme_name
    if theme_path.is_file():
        return theme_path

    # P1-54 fix: walk the full known set of theme-resource locations.
    # Some PBIP reports use SharedResources for base/built-in themes; the
    # old fallback only tried RegisteredResources and silently returned None.
    fallback_locations = [
        report_path / "StaticResources" / "RegisteredResources" / theme_name,
        report_path / "StaticResources" / "SharedResources" / theme_name,
        report_path / "StaticResources" / "SharedResources" / "BaseThemes" / theme_name,
    ]
    for alt in fallback_locations:
        if alt.is_file():
            return alt

    return None


def _load_theme_data(report_path: Path) -> Optional[Dict[str, Any]]:
    """Load and return the raw theme JSON dict."""
    tpath = _resolve_theme_path(report_path)
    if not tpath:
        return None
    return load_json_file(tpath)


def _save_theme_data(report_path: Path, data: Dict[str, Any]) -> bool:
    """Persist the theme JSON back to disk."""
    tpath = _resolve_theme_path(report_path)
    if not tpath:
        logger.error("Cannot determine theme file path for saving")
        return False
    return safe_io.write_json(tpath, data)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_theme(report_path: Path) -> Dict[str, Any]:
    """Load and return the current theme colors, fonts, text_classes, and visual_styles."""
    report_path = _resolve_report_path(report_path)
    tpath = _resolve_theme_path(report_path)
    if not tpath:
        return {
            "success": False,
            "error": "No theme file found. Check themeCollection in report.json.",
        }

    data = load_json_file(tpath)
    if data is None:
        return {"success": False, "error": f"Cannot read theme file: {tpath}"}

    # Extract key sections
    text_classes = data.get("textClasses", {})
    tc_summary = {}
    for cls_name, cls_def in text_classes.items():
        tc_summary[cls_name] = {k: v for k, v in cls_def.items()}

    return {
        "success": True,
        "theme_name": data.get("name", ""),
        "theme_file": str(tpath),
        "colors": {
            "dataColors": data.get("dataColors", []),
            "background": data.get("background", ""),
            "foreground": data.get("foreground", ""),
            "tableAccent": data.get("tableAccent", ""),
            "hyperlink": data.get("hyperlink", ""),
            "good": data.get("good", ""),
            "bad": data.get("bad", ""),
            "neutral": data.get("neutral", ""),
            "maximum": data.get("maximum", ""),
            "center": data.get("center", ""),
            "minimum": data.get("minimum", ""),
        },
        "fonts": {
            "fontFamily": data.get("fontFamily", ""),
        },
        "text_classes": tc_summary,
        "visual_styles": list(data.get("visualStyles", {}).keys()),
    }


def set_colors(report_path: Path, colors: Dict[str, Any]) -> Dict[str, Any]:
    """Set theme colors. Accepted keys: dataColors, background, foreground, good, bad, neutral, etc."""
    report_path = _resolve_report_path(report_path)
    data = _load_theme_data(report_path)
    if data is None:
        return {"success": False, "error": "Cannot load theme file"}

    allowed_keys = {
        "dataColors",
        "background",
        "foreground",
        "good",
        "bad",
        "neutral",
        "maximum",
        "center",
        "minimum",
        "tableAccent",
        "hyperlink",
        "visitedHyperlink",
        "foregroundNeutralSecondary",
        "foregroundNeutralTertiary",
        "backgroundLight",
        "backgroundNeutral",
    }

    updated = []
    for key, value in colors.items():
        if key in allowed_keys:
            data[key] = value
            updated.append(key)
        else:
            logger.warning("Ignoring unknown color key: %s", key)

    if not updated:
        return {"success": False, "error": "No valid color keys provided"}

    if not _save_theme_data(report_path, data):
        return {"success": False, "error": "Failed to save theme file"}

    return {"success": True, "updated_keys": updated, "theme_name": data.get("name", "")}


def _theme_value(value: Any) -> Any:
    """A caller-supplied theme property value -> plain theme.json value (H-21).

    PBIR-shaped values (``{"expr": {"Literal": ...}}``, ``{"solid": {"color":
    {"expr": ...}}}``) are unwrapped; anything already plain passes through.
    Returns ``_SKIP`` for expressions a theme cannot hold (measure-driven, CF).
    """
    if isinstance(value, dict) and ("expr" in value or "solid" in value):
        solid = value.get("solid")
        if isinstance(solid, dict) and isinstance(solid.get("color"), str):
            return value  # already theme-shaped {"solid": {"color": "#hex"}}
        plain = unwrap_for_theme(value)
        return plain if plain is not None else _SKIP
    return value


_SKIP = object()


def _card_props(value: Any) -> Dict[str, Any]:
    """``{prop: v}`` or ``[{prop: v}, ...]`` (theme array form) -> merged dict."""
    if isinstance(value, list):
        merged: Dict[str, Any] = {}
        for item in value:
            if isinstance(item, dict):
                merged.update(item)
        return merged
    if isinstance(value, dict):
        return dict(value)
    raise ValueError(f"card formatting must be an object or an array of objects, got {value!r}")


def _merge_card(style: Dict[str, Any], card: str, props: Dict[str, Any]) -> List[str]:
    """Merge *props* into ``style[card] = [ {...} ]`` (the theme array form)."""
    skipped: List[str] = []
    existing = style.get(card)
    target: Dict[str, Any] = {}
    if isinstance(existing, list) and existing and isinstance(existing[0], dict):
        target = existing[0]
    elif isinstance(existing, dict):
        target = dict(existing)  # repair the old invented dict shape
    for prop, raw in props.items():
        value = _theme_value(raw)
        if value is _SKIP:
            skipped.append(f"{card}.{prop}")
            continue
        target[prop] = copy.deepcopy(value)
    rest = existing[1:] if isinstance(existing, list) else []
    style[card] = [target] + list(rest)
    return skipped


def set_formatting(
    report_path: Path,
    visual_type: str,
    formatting: Dict[str, Any],
) -> Dict[str, Any]:
    """Set theme-level formatting defaults for a visual type in visualStyles.

    Theme shape (real FODASH / Finvision themes): ``visualStyles.<type>.*.
    <card> = [ {prop: plainValue} ]``. ``formatting`` accepts
    ``{card: {prop: value}}`` or ``{card: [{prop: value}]}``.
    """
    report_path = _resolve_report_path(report_path)
    tpath = _resolve_theme_path(report_path)
    if not tpath:
        return {"success": False, "error": "Cannot load theme file"}
    try:
        cards = {card: _card_props(v) for card, v in (formatting or {}).items()}
    except ValueError as e:
        return {"success": False, "error_type": "invalid_args", "error": str(e)}
    out: Dict[str, Any] = {"skipped": []}

    def _mutate(data: Dict[str, Any]) -> None:
        vs = data.setdefault("visualStyles", {})
        wildcard = vs.setdefault(visual_type, {}).setdefault("*", {})
        for card, props in cards.items():
            out["skipped"].extend(_merge_card(wildcard, card, props))
        out["name"] = data.get("name", "")

    safe_io.update_json(tpath, _mutate)
    result = {
        "success": True,
        "visual_type": visual_type,
        "updated_groups": list(cards),
        "theme_name": out.get("name", ""),
    }
    if out["skipped"]:
        result["skipped_non_literal"] = out["skipped"]
    return result


def push_visual_to_theme(
    report_path: Path,
    definition_path: Path,
    page_name: str,
    visual_name: str,
) -> Dict[str, Any]:
    """Push a visual's literal formatting (objects + chrome) as theme defaults for its type.

    Only unselected (or ``{"id": "default"}``) entries are pushed; values are
    unwrapped to plain theme values (``14D`` -> ``14``, ``'x'`` -> ``x``,
    literal colours -> ``{"solid": {"color": "#hex"}}``). Measure-driven /
    CF / theme-colour expressions and content cards (slicer selection,
    textbox text, Deneb spec) are skipped and reported (H-21).
    """
    from core.pbip.authoring.formatting import PROTECTED_OBJECT_CARDS, PROTECTED_PROPERTIES

    report_path = _resolve_report_path(report_path)
    definition_path = Path(definition_path)
    visual_data = _find_visual_data(definition_path, page_name, visual_name)
    if visual_data is None:
        return {"success": False, "error": f"Visual not found: {visual_name} on page {page_name}"}
    visual_section = visual_data.get("visual", {})
    visual_type = visual_section.get("visualType", "")
    if not visual_type:
        return {"success": False, "error": "Could not determine visual type"}
    sections = [
        visual_section.get("objects") or {},
        visual_section.get("visualContainerObjects") or {},
    ]
    cards: Dict[str, Dict[str, Any]] = {}
    skipped: List[str] = []
    for section in sections:
        for card, entries in section.items():
            if card in PROTECTED_OBJECT_CARDS or not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                sel = entry.get("selector")
                if sel and sel != {"id": "default"}:
                    continue
                for prop, value in (entry.get("properties") or {}).items():
                    if prop in PROTECTED_PROPERTIES:
                        continue
                    plain = unwrap_for_theme(value)
                    if plain is None:
                        skipped.append(f"{card}.{prop}")
                        continue
                    cards.setdefault(card, {})[prop] = plain
    if not cards:
        return {
            "success": False,
            "error": "No literal formatting found in visual to push",
            "skipped_non_literal": skipped,
        }
    tpath = _resolve_theme_path(report_path)
    if not tpath:
        return {"success": False, "error": "Cannot load theme file"}
    out: Dict[str, Any] = {}

    def _mutate(data: Dict[str, Any]) -> None:
        wildcard = (
            data.setdefault("visualStyles", {}).setdefault(visual_type, {}).setdefault("*", {})
        )
        for card, props in cards.items():
            _merge_card(wildcard, card, props)
        out["name"] = data.get("name", "")

    safe_io.update_json(tpath, _mutate)
    result = {
        "success": True,
        "visual_type": visual_type,
        "pushed_groups": sorted(cards),
        "theme_name": out.get("name", ""),
    }
    if skipped:
        result["skipped_non_literal"] = skipped
    return result


def list_text_classes(report_path: Path) -> Dict[str, Any]:
    """List text class definitions in the theme (title, label, callout, etc.)."""
    report_path = _resolve_report_path(report_path)
    data = _load_theme_data(report_path)
    if data is None:
        return {"success": False, "error": "Cannot load theme file"}

    raw = data.get("textClasses", {})
    classes: Dict[str, Dict[str, Any]] = {}
    for cls_name, cls_def in raw.items():
        classes[cls_name] = {
            "fontFace": cls_def.get("fontFace"),
            "fontSize": cls_def.get("fontSize"),
            "fontWeight": cls_def.get("fontWeight"),
            "color": cls_def.get("color"),
        }

    return {"success": True, "text_classes": classes, "count": len(classes)}


def set_font(
    report_path: Path,
    text_class: Optional[str] = None,
    font_family: Optional[str] = None,
    font_size: Optional[int] = None,
    color: Optional[str] = None,
    bold: Optional[bool] = None,
) -> Dict[str, Any]:
    """Set font on a text class (e.g. 'title', 'label') or globally when text_class is None."""
    report_path = _resolve_report_path(report_path)
    data = _load_theme_data(report_path)
    if data is None:
        return {"success": False, "error": "Cannot load theme file"}

    updated = []

    if text_class is None:
        # Global font setting
        if font_family is not None:
            data["fontFamily"] = font_family
            updated.append("fontFamily")
        if font_size is not None:
            data["fontSize"] = font_size
            updated.append("fontSize")
        target = "global"
    else:
        # Specific text class
        tc = data.setdefault("textClasses", {}).setdefault(text_class, {})
        if font_family is not None:
            tc["fontFace"] = font_family
            updated.append("fontFace")
        if font_size is not None:
            tc["fontSize"] = font_size
            updated.append("fontSize")
        if color is not None:
            tc["color"] = color
            updated.append("color")
        if bold is not None:
            tc["fontWeight"] = "Bold" if bold else "Normal"
            updated.append("fontWeight")
        target = f"textClass:{text_class}"

    if not updated:
        return {"success": False, "error": "No font properties provided to update"}

    if not _save_theme_data(report_path, data):
        return {"success": False, "error": "Failed to save theme file"}

    return {
        "success": True,
        "target": target,
        "updated_properties": updated,
        "theme_name": data.get("name", ""),
    }


# ---------------------------------------------------------------------------
# Shared internal helpers
# ---------------------------------------------------------------------------


def _merge_deep(base: Dict, overlay: Dict) -> Dict:
    """Recursively merge *overlay* into *base*, returning the merged result."""
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_deep(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _find_visual_data(
    definition_path: Path,
    page_name: str,
    visual_name: str,
) -> Optional[Dict[str, Any]]:
    """Locate and load a visual.json by page name and visual name/ID.

    P1-41 fix: delegates to pbip_utils.find_page_folder /
    find_visual_folder (canonical helpers).
    """
    from core.utilities.pbip_utils import find_page_folder, find_visual_folder

    page_folder = find_page_folder(definition_path, page_name)
    if page_folder is None:
        return None
    vf = find_visual_folder(page_folder, visual_name)
    if vf is None:
        return None
    return load_json_file(vf / "visual.json")


# ---------------------------------------------------------------------------
# T1-G / T1-H: theme apply / export / import / clear_visual_overrides
# ---------------------------------------------------------------------------


def export_theme(report_path: Path, output_path: Path) -> Dict[str, Any]:
    """Write the current theme JSON to ``output_path``.

    The skill's "export theme to share" workflow currently shells out to
    ``pbir theme serialize``. This op produces the same canonical bytes by
    reading from StaticResources directly.
    """
    report_path = _resolve_report_path(report_path)
    data = _load_theme_data(report_path)
    if data is None:
        return {"success": False, "error": "No theme file found in report"}
    output_path = Path(output_path)
    safe_io.allow_root(output_path.parent)
    safe_io.make_dirs(output_path.parent)
    safe_io.write_json(output_path, data)
    return {
        "success": True,
        "exported_to": str(output_path),
        "theme_name": data.get("name", ""),
    }


def apply_theme(
    report_path: Path,
    theme: Dict[str, Any],
    theme_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Overwrite the current theme with the supplied JSON dict.

    Use cases the skills couldn't previously hit:
    - Apply a theme passed in-memory from another tool
    - Apply a normalized theme after edits

    If ``theme_name`` is supplied, it is set on the theme JSON; otherwise the
    existing name is preserved. Does NOT change StaticResources file paths
    or the report.json customTheme reference — use import_theme if the
    incoming theme is a fresh file that needs registering.
    """
    report_path = _resolve_report_path(report_path)
    tpath = _resolve_theme_path(report_path)
    if not tpath:
        return {
            "success": False,
            "error": (
                "No theme file resolvable for this report. Use import_theme "
                "to register a new theme JSON."
            ),
        }
    if not isinstance(theme, dict):
        return {"success": False, "error": "theme must be a JSON object"}
    if theme_name is not None:
        theme["name"] = theme_name
    safe_io.write_json(tpath, theme)
    return {
        "success": True,
        "theme_file": str(tpath),
        "theme_name": theme.get("name", ""),
    }


def import_theme(
    report_path: Path,
    source: Path,
    theme_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Register a theme JSON file from disk under RegisteredResources.

    Copies the file into ``{report}/StaticResources/RegisteredResources/``,
    registers it in ``report.json`` ``resourcePackages[RegisteredResources]
    .items`` as ``type: "CustomTheme"`` (replacing the previous custom-theme
    item — the shape Desktop writes) and points
    ``themeCollection.customTheme`` at it.
    """
    report_path = _resolve_report_path(report_path)
    source = Path(source)
    if not source.is_file():
        return {"success": False, "error": f"Source theme file not found: {source}"}
    theme_data = load_json_file(source)
    if theme_data is None:
        return {"success": False, "error": f"Cannot parse theme JSON: {source}"}
    if not safe_io.is_safe_segment(source.name):
        return {"success": False, "error": f"invalid theme file name: {source.name!r}"}
    final_name = theme_name or theme_data.get("name") or source.stem
    theme_data["name"] = final_name

    dest_dir = report_path / "StaticResources" / "RegisteredResources"
    safe_io.make_dirs(dest_dir)
    dest = dest_dir / source.name
    safe_io.write_json(dest, theme_data)

    report_json_path = report_path / "definition" / "report.json"
    if not report_json_path.exists():
        return {
            "success": False,
            "error": (
                "Theme file written but report.json could not be updated " f"({report_json_path})"
            ),
            "theme_file": str(dest),
        }

    def _mutate(report_data: Dict[str, Any]) -> None:
        tc = report_data.setdefault("themeCollection", {})
        previous = tc.get("customTheme") if isinstance(tc.get("customTheme"), dict) else {}
        custom: Dict[str, Any] = {"name": source.name}
        if previous.get("reportVersionAtImport"):
            custom["reportVersionAtImport"] = previous["reportVersionAtImport"]
        custom["type"] = "RegisteredResources"
        tc["customTheme"] = custom
        packages = report_data.setdefault("resourcePackages", [])
        pkg = next(
            (p for p in packages if isinstance(p, dict) and p.get("type") == "RegisteredResources"),
            None,
        )
        if pkg is None:
            pkg = {"name": "RegisteredResources", "type": "RegisteredResources", "items": []}
            packages.append(pkg)
        items = [
            i
            for i in pkg.get("items") or []
            if not (isinstance(i, dict) and i.get("type") == "CustomTheme")
        ]
        items.append({"name": source.name, "path": source.name, "type": "CustomTheme"})
        pkg["items"] = items

    safe_io.update_json(report_json_path, _mutate)
    return {
        "success": True,
        "theme_file": str(dest),
        "theme_name": final_name,
        "report_json_updated": True,
    }


def _scope_page_dirs(definition_path: Path, scope: str) -> Any:
    """``all`` / ``page:<page>`` / ``visual:<page>/<visual>`` -> ``[(page_dir, visual_id|None)]``."""
    from core.pbip.authoring.targets import resolve_page_dir, resolve_visual_dir

    pages_dir = definition_path / "pages"
    if scope == "all":
        return [(p, None) for p in sorted(pages_dir.iterdir()) if p.is_dir()]
    if scope.startswith("page:"):
        page_dir, err = resolve_page_dir(definition_path, scope[len("page:") :])
        if err:
            return err
        return [(page_dir, None)]
    if scope.startswith("visual:") and "/" in scope:
        page_ref, _, visual_ref = scope[len("visual:") :].partition("/")
        page_dir, err = resolve_page_dir(definition_path, page_ref)
        if err:
            return err
        vdir, verr = resolve_visual_dir(page_dir, visual_ref)
        if verr:
            return verr
        return [(page_dir, vdir.name)]
    return {
        "success": False,
        "error_type": "invalid_args",
        "error": f"scope={scope!r} not understood (use 'all', 'page:<page>' or "
        "'visual:<page>/<visual>')",
    }


def _strip_overrides(objects: Dict[str, Any], keep_cf: bool) -> Dict[str, int]:
    """Remove plain formatting overrides from ``visual.objects`` IN PLACE (H-02).

    Never touched: content cards (general / vega / data / selection / script /
    pages / bookmarks / image ...), content properties (filter, paragraphs,
    jsonSpec, text, url ...), entries scoped by a data / metadata selector
    (per-series colours, per-measure formatting) and — with keep_cf — any
    conditional-formatting value.
    """
    from core.pbip.authoring.formatting import (
        PROTECTED_OBJECT_CARDS,
        PROTECTED_PROPERTIES,
        is_cf_value,
    )

    stats = {"removed": 0, "kept_content": 0, "kept_cf": 0, "kept_selector": 0}
    for card in list(objects):
        entries = objects[card]
        if card in PROTECTED_OBJECT_CARDS or not isinstance(entries, list):
            stats["kept_content"] += 1
            continue
        new_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                new_entries.append(entry)
                continue
            sel = entry.get("selector")
            if sel and sel != {"id": "default"}:
                stats["kept_selector"] += 1
                new_entries.append(entry)
                continue
            props = entry.get("properties") or {}
            kept = {}
            for prop, value in props.items():
                if prop in PROTECTED_PROPERTIES:
                    kept[prop] = value
                    stats["kept_content"] += 1
                elif is_cf_value(value):
                    if keep_cf:
                        kept[prop] = value
                        stats["kept_cf"] += 1
                    else:
                        stats["removed"] += 1
                else:
                    stats["removed"] += 1
            if kept:
                new_entry = dict(entry)
                new_entry["properties"] = kept
                new_entries.append(new_entry)
        if new_entries:
            objects[card] = new_entries
        else:
            del objects[card]
    return stats


def clear_visual_overrides(
    report_path: Path,
    scope: str = "all",
    keep_cf: bool = True,
) -> Dict[str, Any]:
    """Remove per-visual formatting overrides so the theme can take effect.

    H-02: only plain formatting overrides in ``visual.objects`` are removed.
    Slicer selections, textbox text, Deneb specs, slicer mode, images, button
    text, per-selector entries and (``keep_cf``) conditional formatting are
    kept. ``scope``: ``all`` | ``page:<page>`` | ``visual:<page>/<visual>``.
    """
    report_path = _resolve_report_path(report_path)
    definition_path = report_path / "definition"
    pages_dir = definition_path / "pages"
    if not pages_dir.exists():
        return {"success": False, "error": f"No pages folder at {pages_dir}"}
    targets = _scope_page_dirs(definition_path, scope)
    if isinstance(targets, dict):
        return targets

    cleared: List[str] = []
    cleared_visuals: List[Dict[str, str]] = []
    skipped: List[str] = []
    totals = {"removed": 0, "kept_content": 0, "kept_cf": 0, "kept_selector": 0}
    for page_dir, only_vid in targets:
        visuals_dir = page_dir / "visuals"
        if not visuals_dir.exists():
            continue
        for vdir in sorted(visuals_dir.iterdir()):
            if not vdir.is_dir() or (only_vid and vdir.name != only_vid):
                continue
            vj_path = vdir / "visual.json"
            data = load_json_file(vj_path)
            if not data:
                skipped.append(vdir.name)
                continue
            objects = (data.get("visual") or {}).get("objects")
            if not objects:
                continue
            probe = copy.deepcopy(objects)
            stats = _strip_overrides(probe, keep_cf)
            for k, v in stats.items():
                totals[k] += v
            if stats["removed"] == 0:
                continue

            def _mutate(d: Dict[str, Any]) -> None:
                _strip_overrides(d["visual"]["objects"], keep_cf)

            try:
                safe_io.update_json(vj_path, _mutate)
                cleared.append(vdir.name)
                cleared_visuals.append({"page_id": page_dir.name, "visual_name": vdir.name})
            except safe_io.WriteError:
                skipped.append(vdir.name)

    return {
        "success": True,
        "scope": scope,
        "cleared_count": len(cleared),
        "cleared": cleared,
        "cleared_visuals": cleared_visuals[:500],
        "properties_removed": totals["removed"],
        "kept": {
            "content": totals["kept_content"],
            "conditional_formatting": totals["kept_cf"],
            "selector_scoped": totals["kept_selector"],
        },
        "skipped_count": len(skipped),
        "skipped": skipped,
        "keep_cf": keep_cf,
    }
