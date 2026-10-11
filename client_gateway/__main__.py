"""``python -m client_gateway <serve|start|stop|status|mode>``."""

import sys

from client_gateway.cli import _exit_after_grace, main

if __name__ == "__main__":
    rc = main()
    if sys.argv[1:2] == ["serve"]:
        _exit_after_grace(rc)  # the process ends even if a thread hangs on the way out
    sys.exit(rc)
