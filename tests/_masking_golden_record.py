"""Record tests/fixtures/masking/finvision_golden.json (run ONCE at the pre-profile base commit).

python tests/_masking_golden_record.py
"""

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import _masking_golden as golden  # noqa: E402

if __name__ == "__main__":
    data = golden.snapshot()
    with open(golden.FIXTURE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
    print("recorded", golden.FIXTURE)
