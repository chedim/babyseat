"""A tiny stdio MCP server for tests/test_mcp_c1f.py: echo, add, env (reads an environment variable) and slow."""
import os
import time

from mcp.server.mcpserver import MCPServer

server = MCPServer("echo")


@server.tool()
def echo(text: str) -> str:
    """Say the text back."""
    return text


@server.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@server.tool()
def env(name: str) -> str:
    """The value of an environment variable of this server."""
    return os.environ.get(name, "")


@server.tool()
def slow(seconds: float) -> str:
    """Sleep, then answer."""
    time.sleep(seconds)
    return "done"


@server.resource("memo://greeting")
def greeting() -> str:
    """A greeting."""
    return "hello"


if __name__ == "__main__":
    server.run()
