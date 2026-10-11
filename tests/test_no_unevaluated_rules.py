"""Drift guard: every loaded JSON rule must have an evaluator (P0-4).

Prevents rules silently no-oping because their pattern_type has no handler in
JsonRuleEngine._EVALUATORS. Seven `pattern_type:"pattern"` rules were dead
because no "pattern" evaluator existed.
"""

from core.dax.analyzer.rule_engine import JsonRuleEngine


def test_all_loaded_rules_have_an_evaluator():
    engine = JsonRuleEngine()
    handled = set(JsonRuleEngine._EVALUATORS.keys())
    unevaluated = []
    for rule in engine._rules:
        ptype = rule.get("pattern_type")
        if ptype not in handled:
            unevaluated.append((rule.get("rule_id"), ptype))
    assert not unevaluated, f"rules with no evaluator: {unevaluated}"


def test_pattern_rules_are_dispatched_to_a_handler():
    """Every `pattern` rule must have a per-rule handler (no silent skip)."""
    engine = JsonRuleEngine()
    pattern_rules = [r for r in engine._rules if r.get("pattern_type") == "pattern"]
    assert pattern_rules, "expected pattern_type='pattern' rules to exist"
    handlers = JsonRuleEngine._PATTERN_HANDLERS
    missing = [r["rule_id"] for r in pattern_rules if r["rule_id"] not in handlers]
    assert not missing, f"pattern rules with no per-rule handler: {missing}"
