"""Convert docs/GUIDE.md to docs/GUIDE.pdf via markdown + xhtml2pdf.

Usage:
    python build/md_to_pdf.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import markdown
from xhtml2pdf import pisa

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "GUIDE.md"
DEST = ROOT / "docs" / "GUIDE.pdf"

CSS = """
@page {
  size: A4;
  margin: 1.6cm 1.4cm 1.4cm 1.4cm;
  @frame footer { -pdf-frame-content: footer_content;
                  bottom: 0.6cm; margin-left: 1.4cm; margin-right: 1.4cm;
                  height: 0.8cm; }
}
body { font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
       font-size: 9.5pt; line-height: 1.45; color: #1A1A1A; }
h1 { font-size: 22pt; color: #0B3D91; margin-top: 24pt; margin-bottom: 8pt;
     border-bottom: 2px solid #0B3D91; padding-bottom: 4pt;
     page-break-before: always; }
h1.no-pb { page-break-before: auto; }
h2 { font-size: 16pt; color: #0B3D91; margin-top: 18pt; margin-bottom: 6pt; }
h3 { font-size: 13pt; color: #1F4E96; margin-top: 14pt; margin-bottom: 4pt; }
h4 { font-size: 11pt; color: #1F4E96; margin-top: 10pt; margin-bottom: 3pt; }
p  { margin: 4pt 0; }
ul, ol { margin: 4pt 0 4pt 18pt; }
li { margin: 2pt 0; }
code { font-family: "Consolas", "Courier New", monospace; font-size: 8.5pt;
       background: #F4F6F8; padding: 1px 3px; color: #B5306E; }
pre { font-family: "Consolas", "Courier New", monospace; font-size: 8pt;
      background: #F4F6F8; padding: 8pt; border-left: 3px solid #0B3D91;
      white-space: pre-wrap; word-wrap: break-word; margin: 6pt 0; }
pre code { background: transparent; padding: 0; color: #1A1A1A; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0; font-size: 9pt; }
th { background: #0B3D91; color: white; padding: 5pt 6pt; text-align: left;
     font-weight: 600; }
td { border: 1px solid #DDD; padding: 4pt 6pt; vertical-align: top; }
tr:nth-child(even) td { background: #FAFBFC; }
hr { border: 0; border-top: 1px solid #DDD; margin: 12pt 0; }
blockquote { margin: 6pt 0; padding: 6pt 10pt; border-left: 3px solid #0B3D91;
             background: #F4F6F8; }
a { color: #0B3D91; text-decoration: none; }
strong { font-weight: 700; }
.cover { page-break-after: always; text-align: center; padding-top: 4cm; }
.cover h1 { font-size: 36pt; border: none; page-break-before: auto; }
.cover .sub { font-size: 14pt; color: #555; margin-top: 8pt; }
.cover .meta { margin-top: 4cm; color: #777; font-size: 10pt; }
"""


_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def split_frontmatter(md_text: str) -> tuple[dict, str]:
    """Return (frontmatter key/values, markdown body without the YAML block)."""
    m = _FRONTMATTER.match(md_text)
    if not m:
        return {}, md_text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, md_text[m.end() :]


_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+\S")


def separate_lists(md_text: str) -> str:
    """Insert the blank line python-markdown needs before a list that directly follows a
    paragraph line (GitHub renders those lists; python-markdown fuses them into the paragraph).
    Fenced code blocks are left alone."""
    out: list[str] = []
    in_fence = False
    prev = ""
    for line in md_text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif (
            not in_fence
            and _LIST_ITEM.match(line)
            and prev.strip()
            and not prev[0].isspace()
            and not _LIST_ITEM.match(prev)
            and not prev.startswith(("|", "#", ">", "```"))
        ):
            out.append("")
        out.append(line)
        prev = line
    return "\n".join(out)


def registered_tool_count() -> int | None:
    """Tool count from the live registry (None when the server deps are unavailable)."""
    try:
        sys.path.insert(0, str(ROOT))
        from server.handlers import register_all_handlers
        from server.registry import get_registry

        reg = get_registry()
        if reg.total_tools() == 0:
            register_all_handlers(reg)
        return reg.total_tools()
    except Exception:  # noqa: BLE001 - the cover line is cosmetic
        return None


def build_html(md_text: str) -> str:
    meta, md_text = split_frontmatter(md_text)
    md_text = separate_lists(md_text)
    version = meta.get("version", "")
    tools = registered_tool_count()
    md = markdown.Markdown(
        extensions=[
            "fenced_code",
            "tables",
            "toc",
            "sane_lists",
            "attr_list",
        ]
    )
    body = md.convert(md_text)
    cover = (
        '<div class="cover">'
        "<h1 class=\"no-pb\">MCP-PowerBi-Finvision</h1>"
        '<div class="sub">Guide</div>'
        f'<div class="meta">Version {version} &middot; '
        f"{tools if tools is not None else ''} tools<br/>"
        "&copy; Finvision</div>"
        "</div>"
    )
    footer = ('<div id="footer_content" style="text-align:right; '
              'color:#888; font-size:8pt;">'
              f'MCP-PowerBi-Finvision Guide v{version} &middot; '
              '<pdf:pagenumber/> / <pdf:pagecount/></div>')
    return (
        f"<html><head><meta charset='utf-8'>"
        f"<style>{CSS}</style></head><body>"
        f"{cover}{footer}{body}"
        f"</body></html>"
    )


def main() -> int:
    if not SRC.exists():
        print(f"ERROR: source not found: {SRC}", file=sys.stderr)
        return 1
    md_text = SRC.read_text(encoding="utf-8")
    html = build_html(md_text)
    DEST.parent.mkdir(parents=True, exist_ok=True)
    with open(DEST, "wb") as fh:
        result = pisa.CreatePDF(html, dest=fh, encoding="utf-8")
    if result.err:
        print(f"ERROR: PDF generation failed ({result.err} errors)",
              file=sys.stderr)
        return 2
    size_kb = os.path.getsize(DEST) / 1024
    print(f"OK: wrote {DEST} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
