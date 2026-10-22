"""End-to-end test of the extension and the MCP server's bridge client, with a stand-in for the game.

The fake game loads the built extension with ctypes and behaves like the addon: it registers the
callback, drains requests with poll/read, and answers with result/done, passing arguments quoted the
way callExtension does. Instead of running SQF it echoes the request, or answers a few test commands.

Run after building: python3 -m unittest discover -s tests   (A3MCP_LIB overrides the library path)
"""

import asyncio
import ctypes
import json
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "src"))

from arma3_mcp.bridge import Bridge, BridgeError  # noqa: E402
from arma3_mcp.sqf import strip_comments  # noqa: E402

LIB = Path(os.environ.get("A3MCP_LIB", ROOT / "build" / "Release" / "arma3_mcp_x64.so"))
OUTPUT_SIZE = 10240  # the engine's buffer size for callExtension output
CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sqf_arg(value) -> bytes:
    """How callExtension passes an argument: strings quoted with "" escapes, the rest as str."""
    if isinstance(value, bool):
        return b"true" if value else b"false"
    if isinstance(value, bytes):
        return b'"' + value.replace(b'"', b'""') + b'"'
    if isinstance(value, str):
        return sqf_arg(value.encode())
    return str(value).encode()


class FakeGame:
    def __init__(self, lib_path: Path):
        self.lib = ctypes.CDLL(str(lib_path))
        self.lib.RVExtensionArgs.restype = ctypes.c_int
        self.lib.RVExtensionArgs.argtypes = [
            ctypes.c_char_p, ctypes.c_uint, ctypes.c_char_p, ctypes.POINTER(ctypes.c_char_p), ctypes.c_uint,
        ]
        self.wake = threading.Event()
        self.callbacks = 0
        self.polling = threading.Event()
        self.polling.set()
        self._cb = CALLBACK(self._callback)
        self.lib.RVExtensionRegisterCallback(self._cb)
        self.chunks_read = 0
        self.seen = []  # every request handed to the game
        threading.Thread(target=self._loop, daemon=True).start()

    def _callback(self, name, function, data):
        assert name == b"arma3_mcp" and function == b"request"
        self.callbacks += 1
        self.wake.set()
        return 0

    def call(self, function: str, *args):
        out = ctypes.create_string_buffer(OUTPUT_SIZE)
        argv = (ctypes.c_char_p * max(1, len(args)))(*[sqf_arg(a) for a in args])
        rc = self.lib.RVExtensionArgs(out, OUTPUT_SIZE, function.encode(), argv, len(args))
        return out.value, rc

    def _loop(self):
        while True:
            self.wake.wait(0.5)  # like the addon: callback, or the 0.5 s safety poll
            self.wake.clear()
            if self.polling.is_set():
                self.drain()

    def drain(self):
        while True:
            head, _ = self.call("poll")
            if not head:
                return
            rid, size = head.decode().split(":")
            payload, rc = b"", 10
            while rc == 10:
                chunk, rc = self.call("read", rid)
                chunk.decode("utf-8")  # each piece must be valid UTF-8 on its own
                payload += chunk
                self.chunks_read += 1
            assert rc == 0, rc
            assert len(payload) == int(size)
            req = json.loads(payload)
            self.seen.append(req)
            threading.Thread(target=self.handle, args=(rid, req), daemon=True).start()

    def handle(self, rid, req):
        code = req["code"]
        if code.startswith("sleep "):
            time.sleep(float(code.split()[1]))
            result = {"ok": True, "type": "STRING", "result": "slept"}
        elif code.startswith("big "):
            result = {"ok": True, "type": "STRING", "result": "åäö€😀\"\n" * int(code.split()[1])}
        elif code == "fail":
            result = {"ok": False, "error": "boom"}
        else:
            result = {"ok": True, "type": "ARRAY", "result": [code, req["args"], req["scheduled"], req["target"]]}
        self.reply(rid, json.dumps(result, ensure_ascii=False).encode())

    def reply(self, rid, data: bytes):
        # Byte-sized pieces that may split a character, as SQF's select can.
        for i in range(0, len(data), 8000):
            _, rc = self.call("result", rid, data[i : i + 8000])
            if rc != 0:
                return  # the request was dropped (mission restarted); the addon ignores this too
        self.call("done", rid)


GAME: dict = {}


def setUpModule():
    if not LIB.exists():
        raise unittest.SkipTest(f"{LIB} not built")
    port = free_port()
    os.environ["ARMA3_MCP_PORT"] = str(port)
    os.environ["ARMA3_MCP_TOKEN"] = "secret"
    game = FakeGame(LIB)
    text, rc = game.call("start", True, True, False, "test.Stratis", "Stratis")
    assert rc == 0, text
    GAME.update(game=game, port=port, start_text=text.decode())


class GameTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game, cls.port, cls.start_text = GAME["game"], GAME["port"], GAME["start_text"]

    def run_async(self, coro):
        return asyncio.run(coro)

    def bridge(self, token="secret"):
        return Bridge("127.0.0.1", self.port, token)


class BridgeTest(GameTestCase):

    def test_start_message(self):
        self.assertIn(f"listening on 127.0.0.1:{self.port}", self.start_text)
        self.assertIn("token required", self.start_text)
        text, rc = self.game.call("start", True, True, False, "again", "Stratis")
        self.assertEqual(rc, 0, "start is idempotent")

    def test_execute_roundtrip(self):
        async def go():
            b = self.bridge()
            res = await b.execute("count allUnits", {"a": [1, "x\"y"]}, scheduled=True, target="server")
            await b.close()
            return res

        res = self.run_async(go())
        self.assertTrue(res["ok"])
        self.assertEqual(res["result"], ["count allUnits", {"a": [1, 'x"y']}, True, "server"])

    def test_large_unicode_both_ways(self):
        code = "big 30000"  # ~0.4 MB answer
        payload_code = "// " + "ä€😀" * 20000  # ~0.2 MB request, read back in pieces

        async def go():
            b = self.bridge()
            big = await b.execute(code, timeout=30)
            echo = await b.execute(payload_code, timeout=30)
            await b.close()
            return big, echo

        before = self.game.chunks_read
        big, echo = self.run_async(go())
        self.assertEqual(big["result"], "åäö€😀\"\n" * 30000)
        self.assertEqual(echo["result"][0], payload_code)
        self.assertGreater(self.game.chunks_read - before, 10)

    def test_concurrent_requests(self):
        async def go():
            b = self.bridge()
            slow = asyncio.create_task(b.execute("sleep 0.5", scheduled=True))
            fast = [await b.execute(f"n {i}") for i in range(20)]
            res = await slow
            await b.close()
            return fast, res

        fast, slow = self.run_async(go())
        self.assertEqual([r["result"][0] for r in fast], [f"n {i}" for i in range(20)])
        self.assertEqual(slow["result"], "slept")

    def test_error_result_passes_through(self):
        async def go():
            b = self.bridge()
            res = await b.execute("fail")
            await b.close()
            return res

        self.assertEqual(self.run_async(go()), {"ok": False, "error": "boom"})

    def test_status(self):
        async def go():
            b = self.bridge()
            await b.execute("x")
            st = await b.status()
            await b.close()
            return st

        st = self.run_async(go())
        self.assertTrue(st["listening"])
        self.assertTrue(st["callback"])
        self.assertEqual(st["game"]["world"], "Stratis")
        self.assertLess(st["last_poll_ms_ago"], 2000)

    def test_wrong_token_is_refused(self):
        async def go():
            b = self.bridge(token="wrong")
            await b.execute("x")

        with self.assertRaisesRegex(BridgeError, "refused the token"):
            self.run_async(go())

    def test_no_token_is_refused(self):
        async def go():
            b = self.bridge(token="")
            await b.execute("x", timeout=2)

        with self.assertRaises(BridgeError):
            self.run_async(go())

    def raw(self, data: bytes) -> bytes:
        """Sends raw bytes and returns everything the extension answers until it closes."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            s.sendall(data)
            out = b""
            while chunk := s.recv(65536):
                out += chunk
        return out

    def test_unknown_command_closes_before_http_body(self):
        before = len(self.game.seen)
        body = b'EXEC 1 {"code":"from a web page"}\n'
        http = (
            b"AUTH secret\nPOST / HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: text/plain\r\n"
            + b"Content-Length: %d\r\n\r\n" % len(body) + body
        )
        out = self.raw(http)
        self.assertIn(b"ERR unknown command POST", out)
        self.assertNotIn(b"RESULT", out)
        time.sleep(0.7)  # past the fake game's half-second poll
        self.assertEqual(len(self.game.seen), before, "the body's EXEC never reached the game")

    def test_unauthenticated_input_is_capped(self):
        out = self.raw(b"x" * 5000)
        self.assertIn(b"ERR authentication required", out)

    def test_timeout_when_game_not_polling_then_cancelled(self):
        self.game.polling.clear()
        try:

            async def go():
                b = self.bridge()
                try:
                    await b.execute("never", timeout=1)
                finally:
                    st = await b.status()
                    await b.close()
                return st

            with self.assertRaisesRegex(BridgeError, "No answer"):
                self.run_async(go())

            async def queued():
                b = self.bridge()
                st = await b.status()
                await b.close()
                return st

            self.assertEqual(self.run_async(queued())["queued"], 0, "the timed-out request was cancelled")
        finally:
            self.game.polling.set()

    def test_unreachable(self):
        async def go():
            await Bridge("127.0.0.1", free_port()).execute("x")

        with self.assertRaisesRegex(BridgeError, "Cannot reach"):
            self.run_async(go())

    def test_mission_restart_fails_running_requests(self):
        async def go():
            b = self.bridge()
            task = asyncio.create_task(b.execute("sleep 1", scheduled=True))
            await asyncio.sleep(0.3)
            self.game.call("start", True, True, False, "next", "Altis")
            try:
                return await task
            finally:
                await b.close()

        with self.assertRaisesRegex(BridgeError, "mission ended"):
            self.run_async(go())


class McpServerTest(GameTestCase):
    """The MCP tools on top of the same fake game (which echoes instead of running SQF)."""

    def test_tools_registered(self):
        from arma3_mcp import server

        names = {t.name for t in self.run_async(server.mcp.list_tools())}
        self.assertLessEqual({"execute_sqf", "game_status", "mission_info", "get_config", "read_rpt"}, names)

    def test_execute_sqf_tool_strips_comments(self):
        from arma3_mcp import server

        async def go():
            server.bridge.__init__("127.0.0.1", self.port, "secret")  # fresh connection for this event loop
            return await server.execute_sqf("allUnits // every unit", args=[1])

        res = self.run_async(go())
        self.assertEqual(res["result"][0].strip(), "allUnits")
        self.assertEqual(res["result"][1], [1])

    def test_helper_tool_sends_prelude_and_args(self):
        from arma3_mcp import server

        async def go():
            server.bridge.__init__("127.0.0.1", self.port, "secret")
            return await server._run("params ['_a']; _a", ["x"], target="server")

        code, args, scheduled, target = self.run_async(go())
        self.assertIn("private _resolve", code)
        self.assertEqual((args, scheduled, target), (["x"], False, "server"))


class StripCommentsTest(unittest.TestCase):
    def test_strips_comments_outside_strings(self):
        src = 'a = "// not" + \'/* nor */\'; // gone\nb = 1; /* x\ny */ c = """ // still string""";'
        self.assertEqual(strip_comments(src), 'a = "// not" + \'/* nor */\'; \nb = 1; \n c = """ // still string""";')


if __name__ == "__main__":
    unittest.main()
