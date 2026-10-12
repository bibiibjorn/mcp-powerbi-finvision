from core.codemods.plan_hash import compute_plan_hash


class _M:
    def __init__(self, name, expr):
        self.name = name
        self.expression = expr
        self.table_name = "T"


class _T:
    def __init__(self, name, measures):
        self.name = name
        self.measures = measures
        self.columns = []


class _Mod:
    def __init__(self, tables):
        self.tables = tables
        self.relationships = []


def test_plan_hash_is_deterministic():
    m = _Mod([_T("T", [_M("a", "1+1"), _M("b", "2")])])
    assert compute_plan_hash(m) == compute_plan_hash(m)


def test_plan_hash_changes_when_expression_changes():
    m1 = _Mod([_T("T", [_M("a", "1+1")])])
    m2 = _Mod([_T("T", [_M("a", "1+2")])])
    assert compute_plan_hash(m1) != compute_plan_hash(m2)


def test_plan_hash_stable_under_measure_reorder():
    # Measure order is cosmetic in TOM — reordering must NOT change the hash
    # (otherwise a no-op re-read would falsely invalidate a staged plan).
    m1 = _Mod([_T("T", [_M("a", "1"), _M("b", "2")])])
    m2 = _Mod([_T("T", [_M("b", "2"), _M("a", "1")])])
    assert compute_plan_hash(m1) == compute_plan_hash(m2)


# Anti-collision properties (the real staleness-detection concern): any genuine
# content change must change the hash despite the sort.
def test_plan_hash_content_change_rename():
    m1 = _Mod([_T("T", [_M("a", "1")])])
    m2 = _Mod([_T("T", [_M("a_renamed", "1")])])
    assert compute_plan_hash(m1) != compute_plan_hash(m2)


def test_plan_hash_content_change_added_measure():
    m1 = _Mod([_T("T", [_M("a", "1")])])
    m2 = _Mod([_T("T", [_M("a", "1"), _M("b", "2")])])
    assert compute_plan_hash(m1) != compute_plan_hash(m2)


def test_plan_hash_content_change_removed_measure():
    m1 = _Mod([_T("T", [_M("a", "1"), _M("b", "2")])])
    m2 = _Mod([_T("T", [_M("a", "1")])])
    assert compute_plan_hash(m1) != compute_plan_hash(m2)
