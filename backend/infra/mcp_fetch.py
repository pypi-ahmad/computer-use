"""MCP fetch client for Provider web-search planning.

Spawns the official Fetch MCP server over stdio and calls the ``fetch``
tool. Config equivalent::

    {"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}

https://github.com/modelcontextprotocol/servers/tree/main/src/fetch

The Python MCP SDK uses newline-delimited JSON-RPC on stdio, not
Content-Length framing. Fetch MCP retrieves a URL; it is not a search
index. The planner lists public URLs, then this client fetches them.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_DEFAULT_CMD = ("uvx", "mcp-server-fetch")
_MAX_PAGES = 3
_MAX_CHARS = 4000
MCP_FETCH_TOOL_NAME = "mcp_fetch"
MCP_FETCH_DESCRIPTION = (
    "Fetch a public http(s) URL through Fetch MCP (uvx mcp-server-fetch) "
    "and return the page as markdown. Use this to read official docs or "
    "current public pages. Do not fetch localhost or private IPs."
)


def mcp_fetch_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "Public http(s) URL to fetch"}},
        "required": ["url"],
        "additionalProperties": False,
    }


def openai_mcp_fetch_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": MCP_FETCH_TOOL_NAME,
        "description": MCP_FETCH_DESCRIPTION,
        "parameters": mcp_fetch_parameters(),
    }


def anthropic_mcp_fetch_tool() -> dict[str, Any]:
    return {
        "name": MCP_FETCH_TOOL_NAME,
        "description": MCP_FETCH_DESCRIPTION,
        "input_schema": mcp_fetch_parameters(),
    }


def gemini_mcp_fetch_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": MCP_FETCH_TOOL_NAME,
        "description": MCP_FETCH_DESCRIPTION,
        "parameters": mcp_fetch_parameters(),
    }


def mcp_fetch_instruction() -> str:
    return (
        "You may call mcp_fetch with a public https URL to read a page via "
        "Fetch MCP (uvx mcp-server-fetch). Then continue the desktop task "
        "with the computer tool. Do not stop after fetching."
    )


async def fetch_url_for_model(url: str) -> str:
    """Fetch one public URL via MCP for a model tool call."""
    raw = (url or "").strip()
    if not _is_public_http_url(raw):
        return "Error: only public http(s) URLs can be fetched."
    pages = await fetch_pages([raw])
    if not pages:
        return f"Error: Fetch MCP returned no content for {raw}."
    return pages[0]["text"][:_MAX_CHARS]


def public_http_urls(text: str, *, limit: int = _MAX_PAGES) -> list[str]:
    """Return unique public http(s) URLs from *text*, in order."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in _URL_RE.findall(text or ""):
        url = raw.rstrip(".,;:)")
        if url in seen or not _is_public_http_url(url):
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def _is_public_http_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host or host == "localhost" or host.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." in host
    return bool(address.is_global)


def _server_command() -> list[str]:
    override = os.getenv("CUA_MCP_FETCH_CMD", "").strip()
    if override:
        return override.split()
    return list(_DEFAULT_CMD)


async def fetch_pages(urls: list[str]) -> list[dict[str, str]]:
    """Fetch *urls* through one MCP fetch session. Empty list if none or MCP fails."""
    clean = [url for url in urls if _is_public_http_url(url)][:_MAX_PAGES]
    if not clean:
        return []
    try:
        async with _McpFetchSession() as session:
            pages: list[dict[str, str]] = []
            for url in clean:
                text = await session.fetch(url)
                if text:
                    pages.append({"url": url, "text": text})
            return pages
    except Exception:
        logger.warning(
            "Fetch MCP failed (%s); planning continues without pages",
            " ".join(_server_command()),
            exc_info=True,
        )
        return []


class _McpFetchSession:
    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 1

    async def __aenter__(self) -> _McpFetchSession:
        command = _server_command()
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        self._proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "computer-use-workbench", "version": "3.1.1"},
            },
        )
        await self._notify("notifications/initialized", {})
        return self

    async def __aexit__(self, *exc: object) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.stdin:
            proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except TimeoutError:
            proc.kill()
            await proc.wait()

    async def fetch(self, url: str) -> str:
        result = await self._rpc(
            "tools/call",
            {"name": "fetch", "arguments": {"url": url, "max_length": _MAX_CHARS}},
        )
        return _tool_text(result)

    async def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        message_id = self._next_id
        self._next_id += 1
        await self._send({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})
        while True:
            payload = await self._recv()
            if payload.get("id") != message_id:
                continue
            if payload.get("error"):
                raise RuntimeError(str(payload["error"]))
            return payload.get("result")

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _send(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("MCP fetch server is not running")
        proc.stdin.write(_encode_stdio(payload))
        await proc.stdin.drain()

    async def _recv(self) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise RuntimeError("MCP fetch server is not running")
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=45)
            if not line:
                stderr = b""
                if proc.stderr:
                    stderr = await proc.stderr.read()
                raise RuntimeError(
                    "MCP fetch server closed stdout"
                    + (f": {stderr.decode('utf-8', 'replace')}" if stderr else "")
                )
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            return json.loads(text)


def _encode_stdio(payload: dict[str, Any]) -> bytes:
    """Encode one MCP stdio frame (newline-delimited JSON-RPC)."""
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def _tool_text(result: Any) -> str:
    if not isinstance(result, dict):
        return str(result or "").strip()
    parts: list[str] = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
        elif isinstance(item, str):
            parts.append(item)
    return "\n".join(part.strip() for part in parts if part.strip())
