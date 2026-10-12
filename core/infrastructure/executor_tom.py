"""``OptimizedQueryExecutor`` methods split out of ``core.infrastructure.query_executor`` (C-17): pure move, no behaviour change.

``OptimizedQueryExecutor`` inherits :class:`ExecutorTomMixin`, so every ``self.<method>`` call and every import of
``core.infrastructure.query_executor.OptimizedQueryExecutor`` keeps working.
"""

from typing import Any, Dict, List

from core.infrastructure.adomd_utils import engine_message


class ExecutorTomMixin:
    """Methods of :class:`OptimizedQueryExecutor` (executor_tom.py)."""

    def enumerate_m_expressions_tom(self, limit: int | None = None) -> Dict[str, Any]:
        """Enumerate M expressions via TOM as a fallback when DMV is blocked.

        Migrated to ``_amo_session()`` context manager — disconnect is
        guaranteed on every return path without a per-method finally block.
        """
        with self._amo_session() as (server, db):
            if not server or not db:
                return {
                    "success": False,
                    "error": "AMO/TOM unavailable to enumerate expressions",
                    "error_type": "amo_not_available",
                }
            try:
                rows: List[Dict[str, Any]] = []
                model = db.Model
                exprs = getattr(model, "Expressions", None)
                if exprs is not None:
                    for exp in exprs:
                        kind_val = getattr(exp, "Kind", "M")
                        try:
                            kind_str = str(kind_val) if kind_val is not None else "M"
                            if isinstance(kind_str, str) and "." in kind_str:
                                kind_str = kind_str.split(".")[-1]
                        except Exception:
                            kind_str = "M"
                        rows.append(
                            {
                                "Name": getattr(exp, "Name", ""),
                                "Expression": getattr(exp, "Expression", ""),
                                "Kind": kind_str or "M",
                            }
                        )
                        if isinstance(limit, int) and limit > 0 and len(rows) >= limit:
                            break
                if (not rows) and hasattr(model, "DataSources"):
                    for ds in model.DataSources:
                        mexp = getattr(ds, "Expression", None)
                        if mexp:
                            rows.append(
                                {
                                    "Name": getattr(ds, "Name", "DataSource"),
                                    "Expression": mexp,
                                    "Kind": "M",
                                }
                            )
                            if isinstance(limit, int) and limit > 0 and len(rows) >= limit:
                                break
                return {"success": True, "rows": rows, "row_count": len(rows), "method": "TOM"}
            except Exception as e:
                return {
                    "success": False,
                    "error": engine_message(e),
                    "error_type": "tom_enumeration_failed",
                }

    def enumerate_measures_tom(self) -> Dict[str, Any]:
        """Enumerate measures via TOM with Name, Table, Expression."""
        server, db = self._connect_amo_server_db()
        if not server or not db:
            return {
                "success": False,
                "error": "AMO/TOM unavailable",
                "error_type": "amo_not_available",
            }
        try:
            rows: List[Dict[str, Any]] = []
            model = db.Model
            if hasattr(model, "Tables"):
                for tbl in model.Tables:
                    try:
                        if hasattr(tbl, "Measures"):
                            for m in tbl.Measures:
                                rows.append(
                                    {
                                        "Name": getattr(m, "Name", ""),
                                        "Table": getattr(tbl, "Name", ""),
                                        "Expression": getattr(m, "Expression", ""),
                                    }
                                )
                    except Exception:
                        pass
            return {"success": True, "rows": rows, "row_count": len(rows), "method": "TOM"}
        except Exception as e:
            return {"success": False, "error": engine_message(e)}
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    def list_data_sources_tom(self, limit: int | None = None) -> Dict[str, Any]:
        """List data sources via TOM for Desktop compatibility."""
        server, db = self._connect_amo_server_db()
        if not server or not db:
            return {
                "success": False,
                "error": "AMO/TOM unavailable to list data sources",
                "error_type": "amo_not_available",
            }
        try:
            rows: List[Dict[str, Any]] = []
            model = db.Model
            if hasattr(model, "DataSources"):
                for ds in model.DataSources:
                    try:
                        ds_type = type(ds).__name__
                        rows.append(
                            {
                                "DataSourceID": getattr(ds, "Name", None)
                                or getattr(ds, "ID", None)
                                or getattr(ds, "ConnectionName", None),
                                "Name": getattr(ds, "Name", None)
                                or getattr(ds, "ConnectionName", None)
                                or "DataSource",
                                "Description": getattr(ds, "Description", None),
                                "Type": ds_type,
                            }
                        )
                        if isinstance(limit, int) and limit > 0 and len(rows) >= limit:
                            break
                    except Exception:
                        # Continue on per-datasource read errors
                        pass
            # Also crawl partitions to infer data sources when DataSources is sparse
            try:
                if hasattr(model, "Tables"):
                    for tbl in model.Tables:
                        for part in getattr(tbl, "Partitions", []):
                            try:
                                ds = getattr(part, "DataSource", None)
                                if ds:
                                    ds_type = type(ds).__name__
                                    entry = {
                                        "DataSourceID": getattr(ds, "Name", None)
                                        or getattr(ds, "ID", None),
                                        "Name": getattr(ds, "Name", None) or "DataSource",
                                        "Type": ds_type,
                                        "FromPartition": getattr(part, "Name", None),
                                        "Table": getattr(tbl, "Name", None),
                                    }
                                    # Dedup by DataSourceID + Name
                                    if not any(
                                        (
                                            r.get("DataSourceID") == entry["DataSourceID"]
                                            and r.get("Name") == entry["Name"]
                                        )
                                        for r in rows
                                    ):
                                        rows.append(entry)
                            except Exception:
                                pass
            except Exception:
                pass
            # As a last resort, scan model.Expressions for M that declare sources
            try:
                exprs = getattr(model, "Expressions", None)
                if exprs is not None:
                    for exp in exprs:
                        try:
                            name = getattr(exp, "Name", None) or "Expression"
                            kind = str(getattr(exp, "Kind", "M"))
                            if isinstance(kind, str) and "." in kind:
                                kind = kind.split(".")[-1]
                            if kind.upper() == "M":
                                entry = {
                                    "DataSourceID": name,
                                    "Name": name,
                                    "Description": "From Expressions collection",
                                    "Type": "Expression",
                                }
                                if not any(
                                    (
                                        x.get("DataSourceID") == entry["DataSourceID"]
                                        and x.get("Type") == entry["Type"]
                                    )
                                    for x in rows
                                ):
                                    rows.append(entry)
                        except Exception:
                            pass
            except Exception:
                pass
            return {"success": True, "rows": rows, "row_count": len(rows), "method": "TOM"}
        except Exception as e:
            return {"success": False, "error": engine_message(e)}
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass
