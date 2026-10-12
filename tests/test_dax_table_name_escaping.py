"""Regression: table names with an apostrophe must be quote-escaped (doubled)
when interpolated into DAX. A table named `Bob's Sales` otherwise produces
`'Bob's Sales'` — a syntax error (and, for a less-trusted caller, a literal
break-out). DAX escapes a single quote inside a quoted identifier by doubling
it: `'Bob''s Sales'`.
"""

from core.dax.rls_rewriter import build_as_user_query


def test_build_as_user_query_escapes_apostrophe_in_table():
    dax = "EVALUATE Sales"
    role_filters = [{"table": "Bob's Sales", "expression": 'Sales[Region] = "North"'}]
    out = build_as_user_query(dax, role_filters, as_user="alice@acme.com")
    assert "'Bob''s Sales'" in out, f"table apostrophe not escaped: {out!r}"
    # The broken single-escaped form must not appear as the FILTER target.
    assert "FILTER('Bob's Sales'" not in out


def test_build_as_user_query_leaves_prequoted_table_untouched():
    # A caller who already passes a properly-quoted name shouldn't get double-wrapped.
    dax = "EVALUATE Sales"
    role_filters = [{"table": "'Plain Table'", "expression": "Sales[X] = 1"}]
    out = build_as_user_query(dax, role_filters, as_user="a@b.com")
    assert "FILTER('Plain Table'" in out
