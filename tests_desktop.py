"""Standalone tests: python -m unittest -v tests_desktop.py"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types
import unittest
from unittest.mock import patch

from aiohttp import ClientSession, web

from desktop_players import (
    DesktopConfig, DesktopPlayerWatcher, desktop_state_to_payload,
    parse_desktop_players,
)


class FakeCDP:
    """Minimal protocol-compatible target, not a browser or playback service."""

    def __init__(self, *, title="Song A"):
        self.title = title
        self.artist = "Artist A, Artist B"
        self.playing = True
        self.current_time = "1:23"
        self.total_time = "4:10"
        self.track_url = "https://music.yandex.ru/album/42/track/1001"
        self.clients = set()
        self.runner = None
        self.port = None
        self.requests = []

    async def start(self):
        app = web.Application()
        app.router.add_get("/json/list", self.discovery)
        app.router.add_get("/devtools/page/123", self.websocket)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", self.port or 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def stop(self):
        for ws in list(self.clients):
            await ws.close()
        if self.runner:
            await self.runner.cleanup()
            self.runner = None

    async def discovery(self, request):
        return web.json_response([
            {"type": "page", "title": "Other page", "url": "https://example.org", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/wrong"},
            {"type": "page", "title": "Yandex Music", "url": "music-application://desktop/", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/123"},
        ])

    async def websocket(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.clients.add(ws)
        try:
            async for message in ws:
                if message.type.name != "TEXT":
                    continue
                msg = json.loads(message.data)
                method = msg["method"]
                self.requests.append(method)
                result = {}
                if method == "Runtime.evaluate":
                    expr = msg["params"]["expression"]
                    if expr.startswith("!!window.__YM_BRIDGE_CDP_HELPERS_READY__"):
                        result = {"result": {"value": True}}
                    elif "const data = ymCollectAjazzState()" in expr:
                        result = {"result": {"value": {
                            "playerReady": True,
                            "trackTitle": self.title,
                            "trackArtist": self.artist,
                            "trackUrl": self.track_url,
                            "coverUrl": "https://avatars.yandex.net/get-music/cover",
                            "currentTime": self.current_time, "totalTime": self.total_time,
                            "playing": self.playing, "volumePercent": 50,
                            "liked": True, "muted": False,
                        }}}
                await ws.send_json({"id": msg["id"], "result": result})
        finally:
            self.clients.discard(ws)
        return ws

    async def notify(self, *, title, playing=False, current_time="00:02"):
        self.title = title
        self.artist = "Push Artist"
        self.playing = playing
        self.current_time = current_time
        self.total_time = "04:00"
        self.track_url = "https://music.yandex.ru/album/42"
        snapshot = {"playerReady": True, "trackTitle": title,
                    "trackArtist": "Push Artist", "playing": playing,
                    "trackUrl": "https://music.yandex.ru/album/42",
                    "currentTime": current_time, "totalTime": "04:00"}
        for ws in list(self.clients):
            await ws.send_json({"method": "Runtime.bindingCalled", "params": {
                "name": "ymBridgeCdpNotify", "payload": json.dumps(snapshot),
            }})


async def eventually(predicate, timeout=3.0):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.02)
    await asyncio.wait_for(wait(), timeout=timeout)


class DesktopUnitTests(unittest.TestCase):
    def test_parse_and_ids(self):
        config = parse_desktop_players("PC@192.168.1.95:19222, Notebook@[::1]:9222,localhost:9223")
        self.assertEqual([c.name for c in config], ["PC", "Notebook", "Yandex Music localhost:9223"])
        self.assertEqual(config[0].player_id, "desktop:192.168.1.95:19222")
        with self.assertRaises(ValueError):
            parse_desktop_players("A@localhost:9222,B@localhost:9222")
        for invalid in ["bad", "name@host:0", "name@host:90000", "https://example.com:22", "name@x/a:22"]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_desktop_players(invalid)

    def test_multi_scrobbler_pause_adapter_does_not_mutate_others(self):
        import types
        from unittest.mock import patch
        stub_yandex = types.ModuleType("yandex_music")
        stub_yandex.Client = object
        stub_zeroconf = types.ModuleType("zeroconf")
        for name in ("ServiceBrowser", "ServiceStateChange", "Zeroconf"):
            setattr(stub_zeroconf, name, object)
        with patch.dict(sys.modules, {"yandex_music": stub_yandex, "zeroconf": stub_zeroconf}):
            from app import multiscrobbler_pause_compat

        entries = [
            {"player_id": "desktop:a:1", "source": "desktop-cdp", "paused": True, "progress_ms": 2000},
            {"player_id": "desktop:b:1", "source": "desktop-cdp", "paused": True, "progress_ms": 8000},
            {"player_id": "desktop:c:1", "source": "desktop-cdp", "paused": False, "progress_ms": 1000},
            {"player_id": "station:x", "source": "station-local", "paused": True, "progress_ms": 6000},
        ]
        original = {"data": entries, "primary": entries[0], "players_count": 4}
        transformed = multiscrobbler_pause_compat(original)
        self.assertIsNot(transformed, original)
        self.assertIsNot(transformed["data"][0], original["data"][0])
        self.assertEqual(transformed["players_count"], 4)
        self.assertEqual([e["source"] for e in transformed["data"]],
                         ["station-local", "station-local", "desktop-cdp", "station-local"])
        self.assertEqual(transformed["primary"], transformed["data"][0])
        self.assertEqual([e["source"] for e in original["data"]],
                         ["desktop-cdp", "desktop-cdp", "desktop-cdp", "station-local"])
        self.assertEqual([e["player_id"] for e in transformed["data"]],
                         [e["player_id"] for e in original["data"]])

    def test_payload_fields(self):
        cfg = DesktopConfig("Office", "192.168.1.95", 19222)
        st = {"online": True, "title": "Track", "artist": "Name", "playing": False,
              "position": 5.5, "duration": 90, "track_url": "https://music.yandex.ru/album/99"}
        d = desktop_state_to_payload(st, cfg)
        self.assertEqual(d["track_id"], None)
        self.assertEqual(d["progress_ms"], 5500)
        self.assertTrue(d["paused"])
        self.assertEqual(d["player_id"], cfg.player_id)
        st["track_url"] = "https://music.yandex.ru/album/99/track/123"
        self.assertEqual(desktop_state_to_payload(st, cfg)["track_id"], "123")
        st["online"] = False
        self.assertIsNone(desktop_state_to_payload(st, cfg))


class DesktopLiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cdp = FakeCDP()
        await self.cdp.start()

    async def asyncTearDown(self):
        await self.cdp.stop()

    async def test_multi_configured_watchers_and_push(self):
        updates = {}
        config = DesktopConfig("Office", "127.0.0.1", self.cdp.port)
        watcher = DesktopPlayerWatcher(config, lambda player_id, payload: updates.__setitem__(player_id, payload),
                                       poll_interval=0.1, retry_interval=0.1)
        watcher.start()
        try:
            await eventually(lambda: updates.get(config.player_id) is not None)
            self.assertEqual(updates[config.player_id]["title"], "Song A")
            self.assertEqual(updates[config.player_id]["track_id"], "1001")
            self.assertEqual(updates[config.player_id]["progress_ms"], 83000)
            self.assertIn("Page.addScriptToEvaluateOnNewDocument", self.cdp.requests)
            from pathlib import Path
            base = Path(__file__).resolve().parent
            self.assertNotIn("__YM_HA_DESKTOP_OBSERVER__", (base / "desktop_observer.js").read_text())
            self.assertIn("__YM_BRIDGE_DESKTOP_OBSERVER__", (base / "desktop_observer.js").read_text())
            self.assertTrue(watcher.health()["online"])
            await self.cdp.notify(title="Song from push")
            await eventually(lambda: updates.get(config.player_id) and updates[config.player_id]["title"] == "Song from push")
            self.assertEqual(updates[config.player_id]["track_id"], None)  # album URL is not a track ID
            await self.cdp.stop()
            await eventually(lambda: updates.get(config.player_id) is None)
            self.assertFalse(watcher.health()["online"])
        finally:
            await watcher.stop()

    async def test_independent_instances_and_reconnect(self):
        second = FakeCDP(title="Second track")
        await second.start()
        updates = {}
        c1 = DesktopConfig("PC 1", "127.0.0.1", self.cdp.port)
        c2 = DesktopConfig("PC 2", "127.0.0.1", second.port)
        watchers = [DesktopPlayerWatcher(config, lambda pid, value: updates.__setitem__(pid, value),
                                         poll_interval=0.1, retry_interval=0.1)
                    for config in (c1, c2)]
        for watcher in watchers:
            watcher.start()
        try:
            await eventually(lambda: updates.get(c1.player_id) and updates.get(c2.player_id))
            self.assertEqual(updates[c1.player_id]["title"], "Song A")
            self.assertEqual(updates[c2.player_id]["title"], "Second track")
            await self.cdp.stop()
            await eventually(lambda: updates.get(c1.player_id) is None)
            self.assertEqual(updates[c2.player_id]["title"], "Second track")
            self.cdp.title = "Back online"
            await self.cdp.start()  # same TCP port
            await eventually(lambda: updates.get(c1.player_id) and updates[c1.player_id]["title"] == "Back online")
        finally:
            for watcher in watchers:
                await watcher.stop()
            await second.stop()

    async def test_bridge_http_api_with_desktop_only(self):
        stub_yandex = types.ModuleType("yandex_music")
        stub_yandex.Client = object
        stub_zeroconf = types.ModuleType("zeroconf")
        for n in ("ServiceBrowser", "ServiceStateChange", "Zeroconf"):
            setattr(stub_zeroconf, n, object)
        with patch.dict(sys.modules, {"yandex_music": stub_yandex, "zeroconf": stub_zeroconf}):
            import app as bridge_app
            with patch.dict(os.environ, {
                "YM_TOKEN": "", "YM_API_KEY": "demo", "YM_ENABLE_MUSIC": "false",
                "YM_ENABLE_STATIONS": "false", "YM_ENABLE_YNISON": "false",
                "YM_DESKTOP_PLAYERS": f"Office@127.0.0.1:{self.cdp.port}",
                "YM_DESKTOP_POLL_INTERVAL": "0.15", "YM_DESKTOP_RETRY_INTERVAL": "0.1",
                "YM_RESTART_INTERVAL_SECONDS": "0",
            }):
                app = await bridge_app.create_app()
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, "127.0.0.1", 0)
                await site.start()
                port = site._server.sockets[0].getsockname()[1]
                try:
                    async with ClientSession(headers={"X-API-Key": "demo"}) as session:
                        async def get(path):
                            async with session.get(f"http://127.0.0.1:{port}{path}") as r:
                                self.assertEqual(r.status, 200)
                                return await r.json()
                        async def has_one():
                            result = await get("/players")
                            return result["players_count"] == 1
                        async def wait_players():
                            while not await has_one():
                                await asyncio.sleep(0.02)
                        await asyncio.wait_for(wait_players(), timeout=3)
                        snapshot = await get("/players")
                        pid = snapshot["primary"]["player_id"]
                        self.assertEqual(snapshot["primary"]["source"], "desktop-cdp")
                        self.assertEqual((await get("/now-playing?player_id=" + pid))["data"]["title"], "Song A")
                        self.assertIsNone((await get("/now-playing?player_id=desktop:unknown:9222"))["data"])
                        health = await get("/health")
                        self.assertTrue(health["desktop_players"][0]["online"])
                        self.assertFalse(health["music_enabled"])
                        # Multi-Scrobbler's current Yandex Bridge source ignores
                        # paused for desktop-cdp, but supports it for station-local.
                        # Verify /players uses the compatibility adapter without
                        # altering the internal source or the direct API.
                        await self.cdp.notify(title="Song A", playing=False, current_time="01:23")
                        await eventually(lambda: app["bridge"].players.get(pid, {}).get("paused") is True)
                        paused = (await get("/players"))["primary"]
                        self.assertTrue(paused["paused"])
                        self.assertEqual(paused["source"], "station-local")
                        self.assertEqual(paused["actual_source"], "desktop-cdp")
                        self.assertEqual(paused["progress_ms"], 83000)
                        self.assertEqual(paused["player_id"], pid)
                        self.assertEqual((await get("/now-playing?player_id=" + pid))["data"]["source"], "desktop-cdp")
                        await asyncio.sleep(0.1)
                        self.assertEqual((await get("/players"))["primary"]["progress_ms"], 83000)

                        await self.cdp.notify(title="Song A", playing=True, current_time="01:24")
                        await eventually(lambda: app["bridge"].players.get(pid, {}).get("paused") is False and
                                         app["bridge"].players.get(pid, {}).get("progress_ms") == 84000)
                        resumed = (await get("/players"))["primary"]
                        self.assertFalse(resumed["paused"])
                        self.assertEqual(resumed["source"], "desktop-cdp")
                        self.assertEqual(resumed["progress_ms"], 84000)
                        self.assertEqual(resumed["player_id"], pid)

                        await self.cdp.stop()
                        await eventually(lambda: not app["bridge"].players)
                        self.assertEqual((await get("/players"))["players_count"], 0)
                finally:
                    await runner.cleanup()


if __name__ == "__main__":
    unittest.main()
