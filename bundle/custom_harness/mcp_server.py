"""MCP Server CLI Entrypoint."""

import sys
from custom_harness.mcp_grounding.server import run_stdio_server, mcp


def main() -> int:
    """Run MCP server stdio loop."""
    run_stdio_server()
    return 0


if __name__ == "__main__":
    sys.exit(main())
