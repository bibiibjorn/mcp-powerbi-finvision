"""Report which screenshots from _screenshot_worklist.json are still missing.

Run as a module to avoid the tools/visual_catalog/types.py stdlib-shadow:
    python -m tools.visual_catalog.check_captures
"""

import json
import sys
from pathlib import Path


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    worklist = repo / "docs" / "visual-catalog" / "_extracts" / "_screenshot_worklist.json"
    entries = json.loads(worklist.read_text(encoding="utf-8"))

    present, missing = [], []
    for entry in entries:
        out = repo / entry["output_path"].replace("\\", "/")
        (present if out.exists() else missing).append(entry)

    total = len(entries)
    print(f"Screenshots: {len(present)}/{total} captured, {len(missing)} missing")

    if missing:
        print("\nMissing:")
        for e in missing:
            rel = e["output_path"]
            print(f"  - [{e['visual_type']}] {rel}")
        return 1
    print("All screenshots captured. Run build_from_cached_schema to regenerate per-type files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
