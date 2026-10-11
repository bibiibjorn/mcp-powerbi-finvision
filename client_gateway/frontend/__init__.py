"""The TLS front end of the masking gateway (lab only in phase 3a; off unless a front-end port is given).

* ``addon``   the mitmproxy addon: reads the AI-app traffic of the routed hosts, masks a Power BI chat
              through the wire adapters and rehydrates the answer. It holds no engine logic.
* ``runner``  runs mitmproxy in-process, on a loopback port, with a CA that lives in the masking home.

Importing this package does not import mitmproxy; only ``runner.FrontEnd.start`` does.
"""
