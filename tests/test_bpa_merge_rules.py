"""BPAAnalyzer.merge_rules_file: append external (TE-format) BPA rules (Phase 5).

Tabular Editor exports BPA rules as a top-level JSON array; our own format uses
{"rules": [...]}. merge_rules_file accepts BOTH, appends to the existing rule
set (dedup by ID), and never resets — so external rules supplement the
built-ins rather than replacing them.
"""

import json

from core.analysis.bpa_analyzer import BPAAnalyzer


def _write(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_merge_te_format_array(tmp_path):
    analyzer = BPAAnalyzer()
    before = len(analyzer.rules)
    rules_file = tmp_path / "te_rules.json"
    # TE format: top-level array.
    _write(
        rules_file,
        [
            {
                "ID": "EXT_RULE_1",
                "Name": "External Rule 1",
                "Category": "Custom",
                "Description": "d",
                "Severity": 2,
                "Scope": "Measure",
                "Expression": "true",
            }
        ],
    )

    added = analyzer.merge_rules_file(str(rules_file))
    assert added == 1
    assert len(analyzer.rules) == before + 1
    assert any(r.id == "EXT_RULE_1" for r in analyzer.rules)


def test_merge_our_format_object(tmp_path):
    analyzer = BPAAnalyzer()
    rules_file = tmp_path / "our_rules.json"
    _write(
        rules_file,
        {
            "rules": [
                {
                    "ID": "EXT_RULE_2",
                    "Name": "n",
                    "Category": "c",
                    "Description": "d",
                    "Severity": 1,
                    "Scope": "Table",
                    "Expression": "true",
                }
            ]
        },
    )
    added = analyzer.merge_rules_file(str(rules_file))
    assert added == 1
    assert any(r.id == "EXT_RULE_2" for r in analyzer.rules)


def test_merge_dedupes_by_id(tmp_path):
    analyzer = BPAAnalyzer()
    rules_file = tmp_path / "dup.json"
    rule = {
        "ID": "DUP",
        "Name": "n",
        "Category": "c",
        "Description": "d",
        "Severity": 1,
        "Scope": "Table",
        "Expression": "true",
    }
    _write(rules_file, [rule])
    analyzer.merge_rules_file(str(rules_file))
    added_second = analyzer.merge_rules_file(str(rules_file))
    assert added_second == 0  # already present
