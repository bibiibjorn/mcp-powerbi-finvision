"""Internal helpers consumed by registered MCP handlers.

Modules here do NOT register MCP tools — they provide shared functions that
the tool-level handlers in ``server/handlers/`` import. The ``_internal``
namespace signals this: anything in here is an implementation detail and
imports should go through the calling handler's dispatch path.

History: prior to 2026-04-23 these lived next to the registered handlers at
``server/handlers/*`` which made it unclear which files were MCP-surface and
which were internal. Audit item P2-194 tracked the move. One file
(``transaction_management_handler.py``) had zero references and was deleted
outright rather than moved.
"""
