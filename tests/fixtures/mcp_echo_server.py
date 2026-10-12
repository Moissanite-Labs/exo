"""A tiny stdio MCP server for tests: echo, add, boom. Run: python mcp_echo_server.py"""
from mcp.server.mcpserver import MCPServer

server = MCPServer("echo")


@server.tool()
def echo(text: str) -> str:
    """Echo the text back."""
    return text


@server.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@server.tool()
def boom() -> str:
    """Always fails."""
    raise ValueError("boom")


if __name__ == "__main__":
    server.run("stdio")
