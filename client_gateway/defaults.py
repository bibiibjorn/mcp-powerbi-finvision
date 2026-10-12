"""Gateway constants that more than one process needs (stdlib-free: the Claude Code hook loads this
file by path, so it must stay a few plain lines)."""

DEFAULT_PORT = 8890
PORT_ENV = "FINVISION_GATEWAY_PORT"

# ``client_gateway.fail_mode``: what masking does when the gateway or its dictionary is not ready.
# "degrade" (default): mask with everything that is known and report DEGRADED, never refuse a call
# or a chat request. "closed": refuse (the strict behaviour of earlier versions).
FAIL_MODE_DEGRADE = "degrade"
FAIL_MODE_CLOSED = "closed"
FAIL_MODES = (FAIL_MODE_DEGRADE, FAIL_MODE_CLOSED)


def normalise_fail_mode(value: object) -> str:
    """``"closed"`` only for the exact word (any case); every other value is ``"degrade"``."""
    return (
        FAIL_MODE_CLOSED
        if isinstance(value, str) and value.strip().lower() == FAIL_MODE_CLOSED
        else FAIL_MODE_DEGRADE
    )
