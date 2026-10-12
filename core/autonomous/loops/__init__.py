"""Loop orchestrators built on top of core/autonomous/ primitives.

The debug loop composes the Desktop Bridge (in-place reload + screenshot of a
running Power BI Desktop) with the debug-visual op into a closed
debug → fix → reload → verify cycle. v7.8 removed the close/reopen lifecycle
loops; the bridge is the verification surface.
"""
