"""DAX UDF (User-Defined Function) CRUD — DMV reads, TMSL writes (C-18, FG-11).

DAX UDFs (Desktop Sept 2025+, GA at compatibility level 1702) live in the
model's ``functions`` collection. The AMO/TOM client object model does NOT see
them on Desktop with AMO 19.101 / 19.84 (``Model.Functions.Count`` is 0; AMO 19.117,
bundled in ``lib/dotnet/amo-19.117.0``, does see them and writes go through TOM first)
while ``$SYSTEM.TMSCHEMA_FUNCTIONS`` / ``INFO.USERDEFINEDFUNCTIONS()`` return
every function (LIVE-CHECKS 2026-09-22). So:

* **reads** (list / get / collision checks) go through the query executor's
  ``list_udfs()`` DMV read (never ``Model.Functions``);
* **writes** (create / update / delete / daxlib_install) are TMSL commands
  executed server-side via AMO ``Server.Execute`` — the server parses the
  TMSL, so the client object model's blindness to functions does not matter.
  Every write is verified by a fresh DMV read-back;
* every AMO session is disconnected in ``finally`` (C-18: the old code leaked
  one XMLA session per call).

Needs live check (TMSL object path for functions is not in the published TMSL
reference yet): run in DAX Studio / SSMS XMLA against a CL-1702 Desktop model::

    {"createOrReplace": {"object": {"database": "<db>", "function": "Probe"},
                         "function": {"name": "Probe", "expression": "() => 1"}}}
    {"delete": {"object": {"database": "<db>", "function": "Probe"}}}

and confirm ``SELECT [Name] FROM $SYSTEM.TMSCHEMA_FUNCTIONS`` shows / drops it.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from core.operations.amo_helpers import get_server_db_model

logger = logging.getLogger(__name__)


# Try to load TOM at import time so we can detect AMO availability up front.
AMO_AVAILABLE = False
try:
    from core.infrastructure.dll_paths import load_amo_assemblies

    if load_amo_assemblies():
        import Microsoft.AnalysisServices.Tabular as TOM  # type: ignore  # noqa: F401

        AMO_AVAILABLE = True
except Exception as _e:  # pragma: no cover - CLR not present in unit-test env
    logger.debug("AMO/TOM not available for function CRUD: %s", _e)


def _valid_identifier(s: Optional[str]) -> bool:
    return bool(s) and 0 < len(str(s).strip()) <= 128 and "\0" not in str(s)


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes"}
    return bool(v)


class FunctionCrudManager:
    """CRUD operations for DAX UDFs (DMV reads + TMSL writes)."""

    def __init__(self):
        self._last_amo_error: Optional[str] = None

    # ---- read seam ---------------------------------------------------------

    def _udf_rows(self) -> Tuple[Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]]]:
        """(rows, None) from the DMV, or (None, error_dict)."""
        from core.infrastructure.connection_state import connection_state

        if not connection_state.is_connected():
            return None, {
                "success": False,
                "error": "Not connected to Power BI Desktop",
                "error_type": "not_connected",
            }
        qe = connection_state.query_executor
        if qe is None or not hasattr(qe, "list_udfs"):
            return None, {
                "success": False,
                "error": "query executor unavailable",
                "error_type": "manager_unavailable",
            }
        res = qe.list_udfs()
        if not isinstance(res, dict) or not res.get("success"):
            return None, {
                "success": False,
                "error": (res or {}).get("error") or "UDF DMV read failed",
                "error_type": (res or {}).get("error_type") or "udf_read_failed",
            }
        rows = []
        for r in res.get("rows") or []:
            rows.append(
                {
                    (
                        k[1:-1]
                        if isinstance(k, str) and k.startswith("[") and k.endswith("]")
                        else k
                    ): v
                    for k, v in r.items()
                }
            )
        self._last_source = res.get("source")
        return rows, None

    @staticmethod
    def _serialize_row(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": row.get("Name") or "",
            "expression": row.get("Expression") or "",
            "description": row.get("Description") or "",
            "is_hidden": _truthy(row.get("IsHidden")),
            "state": row.get("State"),
            "error_message": row.get("ErrorMessage") or None,
            "lineage_tag": row.get("LineageTag") or None,
            "modified_time": row.get("ModifiedTime"),
        }

    @staticmethod
    def _find_row(rows: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
        lowered = str(name).lower()
        return next((r for r in rows if str(r.get("Name") or "").lower() == lowered), None)

    # ---- write seam --------------------------------------------------------

    def _write_via_tom(
        self,
        action: str,
        name: str,
        expression: Optional[str] = None,
        description: Optional[str] = None,
        is_hidden: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """Write through the TOM object model when the loaded AMO can see UDFs.

        Live 2026-09-23: AMO 19.117 exposes ``Model.Functions`` on Desktop 2.157
        (19.101 / 19.84 see 0) while the TMSL ``createOrReplace.function`` path is
        rejected ("Unrecognized JSON property: function"). Returns None when the
        TOM path is unavailable (old AMO / no Function type) so the caller falls
        back to TMSL; otherwise the success/error envelope. Always disconnects.
        """
        if not AMO_AVAILABLE:
            return None
        try:
            import Microsoft.AnalysisServices.Tabular as TOM  # type: ignore  # noqa: F811
        except Exception:  # noqa: BLE001
            return None
        if not hasattr(TOM, "Function"):
            return None
        from core.infrastructure.connection_state import connection_state

        server, _db, model, err = get_server_db_model(connection_state)
        if server is None or model is None:
            return None
        try:
            functions = getattr(model, "Functions", None)
            if functions is None:
                return None
            rows, _ = self._udf_rows()
            if rows and functions.Count == 0:
                return None  # this AMO build is blind to the model's UDFs
            from core.operations.write_lane import get_write_lane

            with get_write_lane().lock:
                existing = functions.Find(name) if hasattr(functions, "Find") else None
                if action == "create":
                    fn = TOM.Function()
                    fn.Name = name
                    fn.Expression = expression or ""
                    if description:
                        fn.Description = description
                    functions.Add(fn)
                elif action == "update":
                    if existing is None:
                        return None
                    if expression is not None:
                        existing.Expression = expression
                    if description is not None:
                        existing.Description = description
                    if is_hidden is not None and hasattr(existing, "IsHidden"):
                        existing.IsHidden = bool(is_hidden)
                elif action == "delete":
                    if existing is None:
                        return None
                    functions.Remove(existing)
                else:
                    return None
                try:
                    from core.operations.write_lane import commit

                    commit(model, label=f"function/{action}")
                except Exception as e:  # noqa: BLE001
                    try:
                        from core.operations.write_lane import safe_undo

                        safe_undo(model)
                    except Exception:  # noqa: BLE001
                        pass
                    return {
                        "success": False,
                        "error": str(e),
                        "error_type": "tom_error",
                        "via": "tom",
                    }
            try:
                connection_state.bump_model_generation(f"udf tom {action}")
            except Exception:  # noqa: BLE001
                pass
            return {"success": True, "via": "tom"}
        finally:
            try:
                server.Disconnect()
            except Exception:  # noqa: BLE001
                pass

    # ---- write journal (JR-01): the TMSL lane does not go through write_lane.commit ----

    @staticmethod
    def _tmsl_ops(command: Dict[str, Any]) -> List[Dict[str, Any]]:
        seq = command.get("sequence") if isinstance(command, dict) else None
        ops = seq.get("operations") if isinstance(seq, dict) else None
        return [o for o in (ops if isinstance(ops, list) else [command]) if isinstance(o, dict)]

    @staticmethod
    def _row_image(row: Dict[str, Any]) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "name": row.get("Name") or "",
            "expression": row.get("Expression") or "",
        }
        if row.get("Description"):
            body["description"] = row["Description"]
        if _truthy(row.get("IsHidden")):
            body["isHidden"] = True
        if row.get("LineageTag"):
            body["lineageTag"] = row["LineageTag"]
        return body

    def _journal_pre_rows(self, command: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Pre-images (by lower-case name) of the UDFs a TMSL command touches. Never raises."""
        try:
            from core.operations import write_journal

            if not write_journal.is_enabled():
                return {}
            rows, _err = self._udf_rows()
            return {str(r.get("Name") or "").lower(): self._row_image(r) for r in (rows or [])}
        except Exception as e:  # noqa: BLE001
            logger.debug("journal pre-image read failed: %s", e)
            return {}

    def _journal_tmsl(
        self, command: Dict[str, Any], pre_rows: Dict[str, Dict[str, Any]], info: Dict[str, Any]
    ) -> None:
        """Append the journal entry of a TMSL UDF write. Never raises."""
        try:
            from core.operations import write_journal

            changes: List[Dict[str, Any]] = []
            for op in self._tmsl_ops(command):
                if "createOrReplace" in op:
                    body = dict(op["createOrReplace"].get("function") or {})
                    name = str(body.get("name") or "")
                    pre = pre_rows.get(name.lower())
                    changes.append(
                        {
                            "ref": f"function:{name}",
                            "kind": "function",
                            "table": None,
                            "name": name,
                            "change": "modified" if pre is not None else "added",
                            "pre": pre,
                            "post": body,
                        }
                    )
                elif "delete" in op:
                    name = str((op["delete"].get("object") or {}).get("function") or "")
                    pre = pre_rows.get(name.lower())
                    if name and pre is not None:
                        changes.append(
                            {
                                "ref": f"function:{name}",
                                "kind": "function",
                                "table": None,
                                "name": name,
                                "change": "removed",
                                "pre": pre,
                                "post": None,
                            }
                        )
            write_journal.record_external("function/tmsl", changes, info)
        except Exception as e:  # noqa: BLE001
            logger.debug("journal record for TMSL UDF write failed: %s", e)

    def _execute_tmsl(self, command: Dict[str, Any]) -> Dict[str, Any]:
        """Run one TMSL command via AMO ``Server.Execute``; always disconnects.

        ``command`` may contain the placeholder ``"<database>"`` anywhere in an
        ``object`` / ``parentObject`` path; it is replaced by the connected
        database name.
        """
        if not AMO_AVAILABLE:
            return {
                "success": False,
                "error": "AMO not available - cannot write UDFs",
                "error_type": "amo_unavailable",
            }
        from core.infrastructure.connection_state import connection_state

        server, db, _model, err = get_server_db_model(connection_state)
        self._last_amo_error = err
        if server is None or db is None:
            return {
                "success": False,
                "error": f"Could not connect to model: {err or 'unknown cause'}",
                "error_type": "connection_error",
            }
        try:
            text = json.dumps(command, ensure_ascii=False).replace(
                '"<database>"', json.dumps(str(db.Name))
            )
            from core.operations.write_lane import get_write_lane

            pre_rows = self._journal_pre_rows(command)
            with get_write_lane().lock:
                results = server.Execute(text)
            errors: List[str] = []
            for res in list(results or []):
                for msg in list(getattr(res, "Messages", None) or []):
                    if "Error" in type(msg).__name__:
                        errors.append(str(getattr(msg, "Description", msg)))
            if errors or bool(getattr(results, "ContainsErrors", False)):
                return {
                    "success": False,
                    "error": "; ".join(errors) or "TMSL command reported errors",
                    "error_type": "tmsl_error",
                    "tmsl": command,
                }
            try:
                connection_state.bump_model_generation("udf tmsl write")
            except Exception:  # noqa: BLE001
                pass
            out: Dict[str, Any] = {"success": True}
            self._journal_tmsl(command, pre_rows, out)
            return out
        except Exception as e:  # noqa: BLE001 - CLR interop
            return {"success": False, "error": str(e), "error_type": "tmsl_error", "tmsl": command}
        finally:
            try:
                server.Disconnect()
            except Exception:
                pass

    # ---- helpers -------------------------------------------------------------

    @staticmethod
    def _validate_dax_expression(expression: str) -> Optional[str]:
        """Structural pre-write check on the UDF body (delimiter balance only).

        Returns None if acceptable, or a user-facing error message string.
        Balance checks are the strongest assertion that is safe here: they catch
        the common truncation/paste error without second-guessing GA UDF syntax
        (typed parameters, `VAL`/`EXPR` passing modes, optional defaults, `///`
        doc comments). The engine remains the authority on the rest.
        """
        if not expression or not expression.strip():
            return "expression is required (non-empty DAX body)"
        try:
            from core.dax.dax_validator import DaxValidator  # type: ignore

            v = DaxValidator()
            errors: list = []
            errors.extend(v.check_balanced_quotes(expression) or [])
            for open_char, close_char, name in (
                ("(", ")", "parentheses"),
                ("[", "]", "brackets"),
            ):
                errors.extend(
                    v.check_balanced_delimiters(expression, open_char, close_char, name) or []
                )
            if errors:
                return "; ".join(str(e) for e in errors[:3])
        except Exception as e:
            logger.debug("dax_validator unavailable, skipping pre-write validation: %s", e)
        return None

    @staticmethod
    def _with_parameters(expression: str, parameters: Optional[List[Dict[str, Any]]]):
        """(expression, notice). A body without ``=>`` gets a parameter header
        built from ``parameters`` (``name: TYPE``); a full lambda is kept verbatim."""
        if not parameters:
            return expression, None
        if "=>" in expression:
            return expression, (
                "parameters ignored: the expression already declares its parameter list "
                "(GA syntax keeps parameters inside the lambda)."
            )
        parts = []
        for p in parameters:
            pname = str(p.get("name") or "").strip()
            if not pname:
                continue
            ptype = str(p.get("type") or "").strip()
            parts.append(f"{pname}: {ptype}" if ptype and ptype.lower() != "variant" else pname)
        return f"({', '.join(parts)}) =>\n{expression}", None

    @staticmethod
    def _function_body(
        name: str,
        expression: str,
        *,
        description: Optional[str] = None,
        is_hidden: Optional[bool] = None,
        lineage_tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"name": name, "expression": expression}
        if description:
            body["description"] = description
        if is_hidden:
            body["isHidden"] = True
        body["lineageTag"] = lineage_tag or str(uuid.uuid4())
        return body

    @staticmethod
    def _create_or_replace(body: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "createOrReplace": {
                "object": {"database": "<database>", "function": body["name"]},
                "function": body,
            }
        }

    def _verify(
        self, name: str, expression: Optional[str], *, gone: bool = False
    ) -> Optional[bool]:
        rows, err = self._udf_rows()
        if err:
            return None
        row = self._find_row(rows, name)
        if gone:
            return row is None
        if row is None:
            return False
        if expression is not None:
            return (row.get("Expression") or "").strip() == expression.strip()
        return True

    # ---- public ops ---------------------------------------------------------

    def list_functions(self, include: Optional[Any] = None) -> Dict[str, Any]:
        """List UDFs. Like measure list, bodies are left out unless asked for
        (``include=['expression']`` or ``'all'``) — 36 bodies on a real model were 77 KB;
        use ``get`` for one function's full expression."""
        rows, err = self._udf_rows()
        if err:
            return err
        wanted = (
            include if isinstance(include, (list, tuple, set)) else ([include] if include else [])
        )
        wanted = {str(w).lower() for w in wanted}
        with_body = bool(wanted & {"expression", "all"})
        out = [self._serialize_row(r) for r in rows]
        if not with_body:
            for item in out:
                body = item.pop("expression", None)
                if body is not None:
                    item["expression_chars"] = len(body)
        res: Dict[str, Any] = {
            "success": True,
            "functions": out,
            "count": len(out),
            "source": getattr(self, "_last_source", None) or "dmv",
        }
        broken = [f["name"] for f in out if f.get("error_message")]
        if broken:
            res["functions_in_error"] = broken
        return res

    def get_function(self, function_name: str) -> Dict[str, Any]:
        if not _valid_identifier(function_name):
            return {
                "success": False,
                "error": "function_name must be non-empty and <=128 chars",
                "error_type": "invalid_parameters",
            }
        rows, err = self._udf_rows()
        if err:
            return err
        row = self._find_row(rows, function_name)
        if row is None:
            return {
                "success": False,
                "error": f"function {function_name!r} not found",
                "error_type": "not_found",
            }
        return {"success": True, "function": self._serialize_row(row)}

    def create_function(
        self,
        function_name: str,
        expression: str,
        parameters: Optional[List[Dict[str, str]]] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not _valid_identifier(function_name):
            return {
                "success": False,
                "error": "function_name must be non-empty and <=128 chars",
                "error_type": "invalid_parameters",
            }
        if validation_err := self._validate_dax_expression(expression):
            return {"success": False, "error": validation_err, "error_type": "invalid_dax"}
        rows, err = self._udf_rows()
        if err:
            return err
        if self._find_row(rows, function_name) is not None:
            return {
                "success": False,
                "error": f"function {function_name!r} already exists; use update_function",
                "error_type": "already_exists",
            }
        expr, notice = self._with_parameters(expression, parameters)
        body = self._function_body(function_name, expr, description=description)
        res = self._write_via_tom("create", function_name, expr, description=description)
        if res is None:
            res = self._execute_tmsl(self._create_or_replace(body))
        if not res.get("success"):
            return res
        verified = self._verify(function_name, expr)
        out: Dict[str, Any] = {
            "success": True,
            "message": f"function {function_name!r} created",
            "function": {
                "name": function_name,
                "expression": expr,
                "description": description or "",
            },
            "verified": verified,
        }
        if verified is False:
            out["success"] = False
            out["error"] = "TMSL reported success but the function is not visible in the DMV"
            out["error_type"] = "write_not_visible"
        if notice:
            out["notice"] = notice
        return out

    def update_function(
        self,
        function_name: str,
        expression: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not _valid_identifier(function_name):
            return {
                "success": False,
                "error": "function_name must be non-empty and <=128 chars",
                "error_type": "invalid_parameters",
            }
        if expression is not None:
            if validation_err := self._validate_dax_expression(expression):
                return {"success": False, "error": validation_err, "error_type": "invalid_dax"}
        rows, err = self._udf_rows()
        if err:
            return err
        row = self._find_row(rows, function_name)
        if row is None:
            return {
                "success": False,
                "error": f"function {function_name!r} not found",
                "error_type": "not_found",
            }
        new_expr = expression if expression is not None else (row.get("Expression") or "")
        body = self._function_body(
            row.get("Name") or function_name,
            new_expr,
            description=description if description is not None else row.get("Description"),
            is_hidden=_truthy(row.get("IsHidden")),
            lineage_tag=row.get("LineageTag"),
        )
        res = self._write_via_tom("update", body["name"], new_expr, description=description)
        if res is None:
            res = self._execute_tmsl(self._create_or_replace(body))
        if not res.get("success"):
            return res
        verified = self._verify(function_name, new_expr)
        out = {
            "success": verified is not False,
            "message": f"function {function_name!r} updated",
            "function": {
                "name": body["name"],
                "expression": new_expr,
                "description": body.get("description", ""),
            },
            "verified": verified,
        }
        if verified is False:
            out["error"] = "TMSL reported success but the DMV still shows the old body"
            out["error_type"] = "write_not_visible"
        return out

    def delete_function(self, function_name: str) -> Dict[str, Any]:
        if not _valid_identifier(function_name):
            return {
                "success": False,
                "error": "function_name must be non-empty and <=128 chars",
                "error_type": "invalid_parameters",
            }
        rows, err = self._udf_rows()
        if err:
            return err
        row = self._find_row(rows, function_name)
        if row is None:
            return {
                "success": False,
                "error": f"function {function_name!r} not found",
                "error_type": "not_found",
            }
        name = row.get("Name") or function_name
        res = self._write_via_tom("delete", name)
        if res is None:
            res = self._execute_tmsl(
                {"delete": {"object": {"database": "<database>", "function": name}}}
            )
        if not res.get("success"):
            return res
        verified = self._verify(name, None, gone=True)
        return {
            "success": verified is not False,
            "message": f"function {function_name!r} deleted",
            "verified": verified,
            **(
                {
                    "error": "the function is still visible in the DMV",
                    "error_type": "write_not_visible",
                }
                if verified is False
                else {}
            ),
        }

    # ---- daxlib integration ---------------------------------------------

    def daxlib_search(self, query: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        from core.operations.daxlib_client import search_packages

        return search_packages(query=query, limit=int(limit))

    def daxlib_install(
        self,
        package_id: str,
        prefix: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Install a daxlib package as UDFs in ONE TMSL ``sequence`` (C-38:
        bodies validated before anything is written; parameter types kept)."""
        from core.operations.daxlib_client import fetch_package

        if not package_id:
            return {
                "success": False,
                "error": "package_id is required",
                "error_type": "invalid_parameters",
            }
        pkg_res = fetch_package(package_id)
        if not pkg_res.get("success"):
            return pkg_res

        functions_to_install = pkg_res.get("functions", [])
        if not functions_to_install:
            return {
                "success": False,
                "error": f"package {package_id!r} contains no functions",
                "error_type": "empty_package",
            }

        rows, err = self._udf_rows()
        if err:
            return err
        installed_names = {str(r.get("Name") or "").lower(): r for r in rows}

        plan: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        collisions: List[str] = []
        for udf in functions_to_install:
            target_name = (prefix + "." + udf["name"]) if prefix else udf["name"]
            expression = udf.get("expression", "")
            if not expression:
                failed.append({"name": target_name, "error": "no expression"})
                continue
            if (verr := self._validate_dax_expression(expression)) is not None:
                failed.append({"name": target_name, "error": f"invalid body: {verr}"})
                continue
            expr, _notice = self._with_parameters(expression, udf.get("parameters"))
            existing = installed_names.get(target_name.lower())
            if existing is not None:
                collisions.append(target_name)
            plan.append(
                self._function_body(
                    target_name,
                    expr,
                    description=udf.get("description"),
                    lineage_tag=(existing or {}).get("LineageTag"),
                )
            )
        if collisions and not force:
            return {
                "success": False,
                "error": (
                    f"name collisions: {sorted(collisions)}. Pass force=true to overwrite "
                    "or supply a `prefix` to namespace the package."
                ),
                "error_type": "name_collision",
                "collisions": sorted(collisions),
            }
        if failed and not plan:
            return {
                "success": False,
                "error": "no installable functions in the package",
                "error_type": "invalid_package",
                "failed": failed,
            }
        if failed:
            # Validate-before-write: nothing is written when any body is bad.
            return {
                "success": False,
                "error": f"{len(failed)} function(s) failed validation; nothing was installed",
                "error_type": "invalid_package",
                "failed": failed,
            }
        command = {"sequence": {"operations": [self._create_or_replace(b) for b in plan]}}
        res = self._execute_tmsl(command)
        if not res.get("success"):
            return res
        installed = [b["name"] for b in plan]
        return {
            "success": True,
            "package_id": package_id,
            "prefix": prefix,
            "installed": installed,
            "failed": [],
            "stats": {"total": len(functions_to_install), "installed": len(installed), "failed": 0},
        }
