"""MCP client: wrap a third-party MCP server's tools as anthill `Tool`s.

The official `mcp` SDK is async and imported lazily (it needs Python 3.10+). Our
`Tool.fn` is sync and may be called from inside the web event loop, so every MCP call
is bridged to a private event loop on a worker thread (`_run_sync`).

`mcp_tools_for` takes optional `lister`/`caller` callables so the wrapping + governance
can be unit-tested without the SDK (inject fakes); in production they default to the
real SDK-backed `list_tools_raw` / `call_tool_raw`.
"""

from __future__ import annotations

import re

from ..agent.tools import Tool


def mcp_available() -> bool:
    """True iff the official `mcp` SDK can be imported (Python 3.10+ with the extra)."""
    try:
        import mcp  # noqa: F401

        return True
    except Exception:
        return False


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "server"


def _run_sync(coro):
    """Run an async coroutine to completion from sync code, even when an event loop is
    already running (run it on a worker thread with its own loop)."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # no loop here - safe to drive one
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


async def _with_session(server, headers, do):
    """Open an MCP session to `server` (stdio or http), initialize, run `do(session)`."""
    transport = getattr(server, "transport", "http") or "http"
    if transport == "stdio":
        import shlex

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        parts = shlex.split(server.command or "")
        if not parts:
            raise ValueError("stdio MCP server needs a command")
        # Secret env vars (e.g. a Slack bot token) are decrypted by the web layer and attached as
        # `mcp_env`; merge them over the SDK's safe default env so PATH etc. still resolve npx/uvx.
        extra_env = getattr(server, "mcp_env", None)
        if extra_env:
            from mcp.client.stdio import get_default_environment

            params = StdioServerParameters(
                command=parts[0], args=parts[1:], env={**get_default_environment(), **extra_env}
            )
        else:
            params = StdioServerParameters(command=parts[0], args=parts[1:])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await do(session)
    else:
        from mcp import ClientSession

        try:  # the export name has varied across SDK versions
            from mcp.client.streamable_http import streamablehttp_client as _http
        except ImportError:
            from mcp.client.streamable_http import streamable_http_client as _http
        async with _http(server.url, headers=headers or None) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await do(session)


async def _alist(server, headers):
    async def do(session):
        res = await session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": getattr(t, "inputSchema", None) or {},
            }
            for t in res.tools
        ]

    return await _with_session(server, headers, do)


async def _acall(server, headers, name, arguments):
    async def do(session):
        res = await session.call_tool(name, arguments or {})
        parts = [
            getattr(c, "text", "")
            for c in (getattr(res, "content", None) or [])
            if getattr(c, "text", "")
        ]
        if parts:
            return "\n".join(parts)
        return str(getattr(res, "structuredContent", "") or "")

    return await _with_session(server, headers, do)


def list_tools_raw(server, headers=None) -> list[dict]:
    """Live `tools/list` against an MCP server. Raises on failure (the Test endpoint
    surfaces the error)."""
    return _run_sync(_alist(server, headers))


def call_tool_raw(server, headers, name, arguments) -> str:
    """Live `tools/call`. Returns an error string rather than raising, so a failed
    call never crashes the agent loop."""
    try:
        return _run_sync(_acall(server, headers, name, arguments))
    except Exception as e:
        return f"MCP call failed ({name}): {e}"


def mcp_tools_for(server, headers=None, *, lister=None, caller=None) -> list[Tool]:
    """Wrap an MCP server's tools as anthill Tools (prefixed mcp_<slug>_<tool> so they
    fall under the `mcp` scope and never collide across servers). Returns [] on any
    discovery failure so loading approved servers can never break the toolset."""
    lister = lister or list_tools_raw
    caller = caller or call_tool_raw
    slug = _slug(getattr(server, "name", "") or "")
    try:
        specs = lister(server, headers)
    except Exception:
        return []
    tools: list[Tool] = []
    for spec in specs:
        real = spec["name"]
        params = spec.get("inputSchema") or {"type": "object", "properties": {}}

        def _fn(_real=real, **kwargs):
            return caller(server, headers, _real, kwargs)

        tools.append(
            Tool(
                name=f"mcp_{slug}_{real}",
                description=(spec.get("description") or "")[:1000],
                parameters=params,
                fn=_fn,
                needs_approval=bool(getattr(server, "require_approval", False)),
            )
        )
    return tools
