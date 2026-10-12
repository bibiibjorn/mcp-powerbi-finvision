"""Deep review 2026-10-03 (slice N): N-29, a second DEFINE must not be swallowed into a VAR."""

from core.dax.dax_validate import build_bind_only_query

TWO_DEFINES = (
    "DEFINE MEASURE T[A] = 1\n"
    'EVALUATE ROW("a", [A])\n'
    "DEFINE MEASURE T[B] = 2\n"
    'EVALUATE ROW("b", [B])'
)


def test_second_define_is_reported_not_validated_instead_of_swallowed():
    plan = build_bind_only_query(TWO_DEFINES)
    assert plan.ok and plan.statements == 1
    assert plan.query.count("DEFINE") == 1  # the second DEFINE text is not in the rewrite
    assert "MEASURE T[B]" not in plan.query
    assert plan.dropped and "second DEFINE (line 3)" in plan.dropped[0]


def test_first_statement_is_still_bound_in_full():
    plan = build_bind_only_query(TWO_DEFINES)
    assert 'VAR __mcp_validate_1 = ROW("a", [A])' in plan.query
    assert plan.query.rstrip().endswith(
        'EVALUATE ROW("__mcp_validate", IF(FALSE(), COUNTROWS(__mcp_validate_1), 0))'
    )


def test_second_define_after_several_evaluates_and_order_by():
    plan = build_bind_only_query(
        "EVALUATE 'A' ORDER BY 'A'[x]\nEVALUATE 'B'\nDEFINE VAR z = 1\nEVALUATE ROW(\"z\", z)"
    )
    assert plan.ok and plan.statements == 2
    assert "VAR z" not in plan.query
    assert any("ORDER BY" in d for d in plan.dropped) and any(
        "second DEFINE" in d for d in plan.dropped
    )


def test_single_define_query_unchanged():
    plan = build_bind_only_query('DEFINE MEASURE T[A] = 1\nEVALUATE ROW("a", [A])')
    assert plan.ok and plan.dropped == []


def test_define_inside_a_string_or_comment_is_not_a_second_define():
    plan = build_bind_only_query('EVALUATE ROW("DEFINE", 1) // DEFINE here\n')
    assert plan.ok and plan.dropped == []
