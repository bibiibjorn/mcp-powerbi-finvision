"""Gateway constants that more than one process needs (stdlib-free: the Claude Code hook loads this
file by path, so it must stay a few plain lines)."""

DEFAULT_PORT = 8890
PORT_ENV = "FINVISION_GATEWAY_PORT"
