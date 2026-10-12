"""WP-5A: S-32 (16_Dependency_Graph direction names), F-17 retirement (07 compare_models runs the
one compare engine; classification never drops an unrecognised change) and the F-27 ``request_id``
declarations on the inline-schema writer tools.
"""

from __future__ import annotations

import pytest

from core.comparison import compare_targets as ct
from core.graph.models import Edge, Graph, Node
from server.arg_aliases import normalize_tool_args
from server.handlers import dependency_graph_handler as dgh
from server.registry import HandlerRegistry


# ---------------------------------------------------------------------------
# S-32: get_consumers / get_dependencies
# ---------------------------------------------------------------------------
def _graph():
    g = Graph()
    g.add_node(Node(kind="column", scoped_name="Sales[Amount]"))
    g.add_node(Node(kind="measure", scoped_name="Sales[Total]"))
    g.add_node(Node(kind="measure", scoped_name="Sales[Avg]"))
    g.add_edge(Edge(src="measure:Sales[Total]", dst="column:Sales[Amount]", kind="references"))
    g.add_edge(Edge(src="measure:Sales[Avg]", dst="measure:Sales[Total]", kind="references"))
    return g


@pytest.fixture
def graph(monkeypatch):
    g = _graph()
    monkeypatch.setattr(dgh, "_get_graph", lambda gid: (g, None))
    monkeypatch.setattr(dgh, "_freshness", lambda graph_: {"stale": False})
    return g


def _q(op, ref):
    return dgh.handle_dependency_graph({"operation": op, "graph_id": "g", "node_ref": ref})


def test_get_consumers_is_what_depends_on_the_node(graph):
    out = _q("get_consumers", "measure:Sales[Total]")
    assert out["success"] is True
    assert out["consumers"] == ["measure:Sales[Avg]"]  # Avg USES Total -> it is a consumer
    assert out["ancestors"] == out["consumers"]


def test_get_dependencies_is_what_the_node_depends_on(graph):
    out = _q("get_dependencies", "measure:Sales[Total]")
    assert out["dependencies"] == ["column:Sales[Amount]"]
    assert out["descendants"] == out["dependencies"]


def test_the_legacy_names_return_the_same_walk(graph):
    assert _q("get_upstream", "measure:Sales[Total]")["consumers"] == ["measure:Sales[Avg]"]
    assert _q("get_downstream", "measure:Sales[Total]")["dependencies"] == ["column:Sales[Amount]"]


def test_legacy_op_and_direction_are_rewritten_with_a_notice():
    args, notes = normalize_tool_args(
        "16_Dependency_Graph", {"operation": "get_upstream", "graph_id": "g", "node_ref": "x"}
    )
    assert args["operation"] == "get_consumers"
    assert "get_upstream -> get_consumers" in notes["deprecated_args"]["call_form"]
    args, _ = normalize_tool_args(
        "16_Dependency_Graph",
        {"operation": "quick_lineage", "node_ref": "x", "direction": "downstream"},
    )
    assert args["direction"] == "dependencies"
    # canonical spellings pass through untouched, no notice
    args, notes = normalize_tool_args(
        "16_Dependency_Graph", {"operation": "get_dependencies", "direction": "consumers"}
    )
    assert args == {"operation": "get_dependencies", "direction": "consumers"} and not notes


def test_quick_lineage_accepts_the_new_direction_words(monkeypatch):
    g = _graph()
    monkeypatch.setattr(
        dgh,
        "_get_or_build",
        lambda args: {"success": True, "graph_id": "g", "graph": g, "cached": False},
    )
    monkeypatch.setattr(dgh, "_freshness", lambda graph_: {"stale": False})
    out = dgh.handle_dependency_graph(
        {"operation": "quick_lineage", "node_ref": "measure:Sales[Total]", "direction": "consumers"}
    )
    assert out["success"] is True and "upstream" in out and "downstream" not in out
    assert out["upstream"]["ancestors"] == ["measure:Sales[Avg]"]
    bad = dgh.handle_dependency_graph(
        {"operation": "quick_lineage", "node_ref": "x", "direction": "sideways"}
    )
    assert bad["valid_directions"] == ["consumers", "dependencies", "both", "impact"]


def test_the_schema_shows_only_the_new_names_and_both_are_read_ops():
    from server import op_classification as oc
    from server.handlers import register_all_handlers

    reg = HandlerRegistry()
    register_all_handlers(reg)
    props = reg.get_full_schema("16_Dependency_Graph")["properties"]
    ops = props["operation"]["enum"]
    assert "get_consumers" in ops and "get_dependencies" in ops
    assert "get_upstream" not in ops and "get_downstream" not in ops
    assert props["direction"]["enum"] == ["consumers", "dependencies", "both", "impact"]
    for op in ("get_consumers", "get_dependencies", "get_upstream", "get_downstream"):
        assert oc.classify("16_Dependency_Graph", {"operation": op}) == "read"


# ---------------------------------------------------------------------------
# F-17: the one compare engine
# ---------------------------------------------------------------------------
def test_classify_never_drops_a_change_shape_it_does_not_know():
    diff = {
        "roles": {
            "added": [],
            "removed": [],
            "modified": [
                {
                    "name": "Region Reader",
                    "changes": {"filter_expressions": {"changed_tables": ["T"], "details": {}}},
                }
            ],
        },
        "model_properties": {"model": {"compatibility_level": {"from": 1702, "to": 1605}}},
        "tables": {
            "modified": [
                {
                    "name": "Sales",
                    "changes": {
                        "has_changes": True,
                        "own_changes": False,
                        "measures": {
                            "modified": [
                                {
                                    "name": "M",
                                    "changes": {
                                        "description": {"from": "a", "to": "b"},
                                        "expression": {"from": "1", "to": "2"},
                                    },
                                }
                            ]
                        },
                        "properties": {"description": {"from": "x", "to": "y"}},
                    },
                }
            ]
        },
    }
    out = ct.classify_changes(diff)
    affects = {(r["category"], r["object"]): r for r in out["affects_results"]}
    cosmetic = {(r["category"], r["object"]): r for r in out["cosmetic"]}
    assert affects[("roles", "Region Reader")]["fields"] == ["filter_expressions"]
    assert ("model_properties", "model.compatibility_level") in affects
    assert affects[("measures", "Sales.M")]["fields"] == ["expression"]
    assert cosmetic[("measures", "Sales.M")]["fields"] == ["description"]
    assert cosmetic[("tables", "Sales")]["fields"] == ["description"]  # a group of field diffs


def test_compare_models_goes_through_compare_targets(monkeypatch):
    from server.handlers.pbip_operations_handler import handle_pbip_operations

    seen = []

    def load(kind, spec):
        seen.append((kind, dict(spec)))
        base = {"tables": [{"name": "Sales", "measures": [{"name": "M", "expression": "1"}]}]}
        if spec.get("git_ref") is None and spec["pbip_path"].endswith("new"):
            base["tables"][0]["measures"][0]["expression"] = "2"
        return {
            "model": base,
            "compatibility_level": 1702,
            "label": spec["pbip_path"],
            "source": dict(spec),
        }

    monkeypatch.setattr(ct, "load_target", load)
    out = handle_pbip_operations(
        {
            "operation": "compare_models",
            "source_path": "C:/r/old",
            "target_path": "C:/r/new",
            "from_ref": "HEAD",
        }
    )
    assert out["success"] is True
    assert seen == [
        ("git", {"git_ref": "HEAD", "pbip_path": "C:/r/old"}),
        ("pbip", {"pbip_path": "C:/r/new"}),
    ]
    # the keys it always had + the F-17 split
    assert out["comparison"]["has_changes"] is True
    assert {"summary", "affects_results", "cosmetic", "sources"} <= set(out)
    assert out["affects_results"][0]["fields"] == ["expression"]
    assert "reports" not in out  # no HTML written by default


def test_compare_models_passes_target_errors_through(monkeypatch):
    from server.handlers.pbip_operations_handler import handle_pbip_operations

    def load(kind, spec):
        raise ct.TargetError("C:/nope does not exist", "not_found")

    monkeypatch.setattr(ct, "load_target", load)
    out = handle_pbip_operations(
        {"operation": "compare_models", "source_path": "C:/nope", "target_path": "C:/nope2"}
    )
    assert out["success"] is False and out["error_type"] == "not_found"


def test_tmdl_semantic_diff_only_feeds_the_html_report():
    """No tool op runs the second engine any more; the one remaining user is the HTML report's
    'TMDL changes view' (it renders the semantic shape of the parsed TMDL)."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    users = []
    for sub in ("core", "server", "src"):
        for path in (root / sub).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "TmdlSemanticDiff" in text and path.name != "tmdl_semantic_diff.py":
                users.append(path.relative_to(root).as_posix())
    assert users == ["core/comparison/model_diff_report.py"]


# ---------------------------------------------------------------------------
# F-27: request_id on the inline-schema writers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tool", ["11_PBIP_Authoring", "15_DAX_Refactor", "17_Desktop_Bridge", "02_QA_Linguistic"]
)
def test_request_id_is_declared_and_deferred(tool):
    from server.handlers import register_all_handlers
    from server.tool_surface import deferred_args

    reg = HandlerRegistry()
    register_all_handlers(reg)
    full = reg.get_full_schema(tool)["properties"]
    assert full["request_id"]["type"] == "string" and full["request_id"]["maxLength"] == 128
    assert "request_id" in deferred_args(tool)
    assert "request_id" not in reg.get_emitted_schema(tool)["properties"]
