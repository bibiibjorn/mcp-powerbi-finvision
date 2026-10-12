"""
Sub-modules for server.handlers.debug_handler.

The parent module (debug_handler.py) hosts shared helpers and the
visual/compare/drill/analyze/run_dax/optimize operations; this package
holds operations that are cleanly self-contained:

- audit:  handle_audit + handle_debug_loop + audit helpers
- tools:  handle_validate + handle_profile + handle_document (each a
          tiny dispatcher into core.debug.debug_operations)

Keeping sub-modules here (rather than flat siblings of debug_handler.py)
mirrors the P2-194 convention for internal handler helpers.
"""
