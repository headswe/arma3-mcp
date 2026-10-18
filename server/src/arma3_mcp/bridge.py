"""Client for the arma3_mcp extension's TCP bridge (protocol: see src/bridge.hpp)."""

from __future__ import annotations

import asyncio
import itertools
import json
from typing import Any


class BridgeError(Exception):
    """The game could not run a request: not reachable, timed out, or the request was dropped."""


class Bridge:
    def __init__(self, host: str = "127.0.0.1", port: int = 9120, token: str = "", connect_timeout: float = 3.0):
        self.host = host
        self.port = port
        self.token = token
        self.connect_timeout = connect_timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    async def _connect(self) -> None:
        async with self._connect_lock:
            if self._writer is not None and not self._writer.is_closing():
                return
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port, limit=256 * 1024 * 1024), self.connect_timeout
                )
            except (OSError, asyncio.TimeoutError) as exc:
                raise BridgeError(
                    f"Cannot reach the Arma 3 extension at {self.address} ({exc}). Is the game running with "
                    "@arma3_mcp loaded and a mission (editor preview, singleplayer, hosted or dedicated) started?"
                ) from None
            if self.token:
                writer.write(f"AUTH {self.token}\n".encode())
                await writer.drain()
                line = (await reader.readline()).decode(errors="replace").strip()
                if line != "OK auth":
                    writer.close()
                    raise BridgeError(f"The extension refused the token: {line or 'connection closed'}")
            self._reader, self._writer = reader, writer
            self._read_task = asyncio.create_task(self._read_loop(reader))

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                kind, _, rest = raw.decode("utf-8", errors="replace").rstrip("\r\n").partition(" ")
                rid, _, body = rest.partition(" ")
                fut = self._pending.pop(rid, None)
                if fut is None or fut.done():
                    continue
                if kind == "ERROR":
                    fut.set_exception(BridgeError(body))
                elif kind == "PONG":
                    fut.set_result(None)
                else:  # RESULT, STATUS
                    try:
                        fut.set_result(json.loads(body))
                    except json.JSONDecodeError as exc:
                        fut.set_exception(BridgeError(f"the game sent invalid JSON ({exc}): {body[:500]}"))
        except (OSError, asyncio.IncompleteReadError, ValueError):
            pass
        finally:
            if self._writer is not None:
                self._writer.close()
            self._writer = None
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(BridgeError("lost the connection to the game"))
            self._pending.clear()

    async def _request(self, command: str, body: str | None, timeout: float) -> Any:
        await self._connect()
        rid = str(next(self._ids))
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        line = f"{command} {rid}" + (f" {body}" if body is not None else "")
        async with self._write_lock:
            assert self._writer is not None
            self._writer.write(line.encode() + b"\n")
            await self._writer.drain()
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            if command == "EXEC" and self._writer is not None:
                async with self._write_lock:
                    self._writer.write(f"CANCEL {rid}\n".encode())
            raise

    async def status(self, timeout: float = 5.0) -> dict:
        try:
            return await self._request("STATUS", None, timeout)
        except asyncio.TimeoutError:
            raise BridgeError("the extension did not answer STATUS") from None

    async def execute(
        self, code: str, args: Any = None, *, scheduled: bool = False, target: str | int = "local", timeout: float = 15.0
    ) -> dict:
        """Runs SQF in the game; returns {"ok", "type", "result", "time_ms"} or {"ok": false, "error"}."""
        request = {"code": code, "args": [] if args is None else args, "scheduled": scheduled, "target": target}
        try:
            return await self._request("EXEC", json.dumps(request, ensure_ascii=False), timeout)
        except asyncio.TimeoutError:
            hint = ""
            try:
                st = await self.status(2.0)
                age = st.get("last_poll_ms_ago")
                if age is None or age > 3000:
                    hint = " The game is not polling for requests: no mission seems to be running."
                elif scheduled:
                    hint = " The game is polling, so the scheduled script is probably still running (or waiting)."
                else:
                    hint = " The game is polling, so the code may still be running on a remote machine."
            except BridgeError:
                pass
            raise BridgeError(f"No answer from the game within {timeout:g}s.{hint}") from None

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
        if self._read_task is not None:
            self._read_task.cancel()
