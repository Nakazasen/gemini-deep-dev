"""Documentation-grounding internals for the canonical Deep Dev MCP server.

Import concrete symbols from their defining modules. Keeping this package
initializer side-effect free prevents circular server imports and preserves a
fast MCP stdio startup path.
"""
