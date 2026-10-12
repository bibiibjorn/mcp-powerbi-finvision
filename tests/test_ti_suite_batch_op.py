"""`03_Batch_Operations` time-intelligence suite op (generate + optional apply).

Generation is pure/read-only; `apply=true` must reuse the existing gated
measures/create batch path rather than opening a second write path.
"""

from __future__ import annotations


from server.handlers import batch_operations_handler as boh


def _gen(**over):
    args = {
        "object_type": "ti_suite",
        "base_measures": ["Sales"],
        "date_column": "'Date'[Date]",
    }
    args.update(over)
    return boh.handle_batch_operations(args)


class TestPreviewIsReadOnly:
    def test_returns_plan_without_applying(self):
        res = _gen(variants=["YTD", "PY", "DELTA"])
        assert res["success"] is True
        assert res["applied"] is False
        assert res["count"] == 3
        assert "re-call with apply=true" in res["hint"]

    def test_generated_dax_is_correct(self):
        res = _gen(variants=["YTD", "PY", "DELTA"])
        by_name = {m["name"]: m["expression"] for m in res["measures"]}
        assert by_name["Sales YTD"] == "TOTALYTD([Sales], 'Date'[Date])"
        assert by_name["Sales PY"] == "CALCULATE([Sales], SAMEPERIODLASTYEAR('Date'[Date]))"
        assert by_name["Sales vs PY"] == "[Sales] - [Sales PY]"

    def test_no_model_write_occurs_on_preview(self, monkeypatch):
        """The pure path must not reach the batch executor at all."""
        called = []
        monkeypatch.setattr(
            boh._batch_ops_handler,
            "execute",
            lambda *a, **k: called.append(1) or {"success": True},
        )
        _gen()
        assert called == []

    def test_batch_operation_alias_also_routes(self):
        res = boh.handle_batch_operations(
            {
                "batch_operation": "generate_ti_suite",
                "base_measures": ["Sales"],
                "date_column": "'Date'[Date]",
            }
        )
        assert res["success"] is True
        assert res["applied"] is False

    def test_week_variant_surfaces_requirement(self):
        res = _gen(variants=["WTD"])
        assert "CL 1701" in res["requires"]["WTD"]

    def test_optional_metadata_forwarded(self):
        res = _gen(
            variants=["YTD"],
            target_table="_Measures",
            display_folder="TI",
            format_string="#,0",
        )
        m = res["measures"][0]
        assert m["table_name"] == "_Measures"
        assert m["display_folder"] == "TI"
        assert m["format_string"] == "#,0"

    def test_single_string_base_measure_accepted(self):
        res = _gen(base_measures="Sales", variants=["YTD"])
        assert res["count"] == 1


class TestValidation:
    def test_missing_base_measures(self):
        res = boh.handle_batch_operations({"object_type": "ti_suite"})
        assert res["success"] is False
        assert res["error_type"] == "invalid_parameters"

    def test_unqualified_date_column_rejected(self):
        res = _gen(date_column="Date")
        assert res["success"] is False
        assert res["error_type"] == "invalid_parameters"

    def test_missing_date_column_rejected(self):
        res = boh.handle_batch_operations({"object_type": "ti_suite", "base_measures": ["Sales"]})
        assert res["success"] is False

    def test_unknown_variant_lists_known_ones(self):
        res = _gen(variants=["NOPE"])
        assert res["success"] is False
        assert "YTD" in res["known_variants"]


class TestApplyDelegatesToGatedBatchPath:
    def test_apply_routes_through_measures_create(self, monkeypatch):
        seen = {}

        def _fake_execute(args):
            seen.update(args)
            return {"success": True, "created": len(args.get("items", []))}

        monkeypatch.setattr(boh._batch_ops_handler, "execute", _fake_execute)
        # C-14: without target_table the base measure's home table is resolved.
        monkeypatch.setattr(boh, "_resolve_home_tables", lambda names: {n: "Fact" for n in names})
        res = _gen(variants=["YTD", "PY"], apply=True)

        # Same path as an ordinary bulk measure create — not a bespoke writer.
        assert seen["operation"] == "measures"
        assert seen["batch_operation"] == "create"
        assert len(seen["items"]) == 2
        # C-14: the keys the batch executor actually reads.
        assert all(
            i["measure_name"] and i["table_name"] == "Fact" and i["expression"]
            for i in seen["items"]
        )
        assert res["success"] is True
        assert res["ti_suite"]["count"] == 2

    def test_apply_without_resolvable_home_table_refuses(self, monkeypatch):
        called = []
        monkeypatch.setattr(boh._batch_ops_handler, "execute", lambda a: called.append(a))
        monkeypatch.setattr(boh, "_resolve_home_tables", lambda names: {n: None for n in names})
        res = _gen(variants=["YTD"], apply=True)
        assert res["success"] is False
        assert res["error_type"] == "home_table_unresolved"
        assert called == []

    def test_apply_with_empty_plan_is_rejected(self, monkeypatch):
        monkeypatch.setattr(boh._batch_ops_handler, "execute", lambda a: {"success": True})
        # DELTA alone generates nothing (its PY sibling wasn't requested).
        res = _gen(variants=["DELTA"], apply=True)
        assert res["success"] is False
        assert res["error_type"] == "empty_plan"

    def test_apply_strips_generator_only_args_before_delegating(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            boh._batch_ops_handler,
            "execute",
            lambda a: seen.update(a) or {"success": True},
        )
        _gen(variants=["YTD"], apply=True, target_table="_Measures")
        for leaked in ("base_measures", "variants", "apply"):
            assert leaked not in seen, f"{leaked} leaked into the batch args"

    def test_apply_preserves_warnings_from_generation(self, monkeypatch):
        monkeypatch.setattr(boh._batch_ops_handler, "execute", lambda a: {"success": True})
        res = _gen(variants=["YTD", "DELTA"], apply=True, target_table="_Measures")
        assert any("requires variant" in w for w in res["warnings"])
