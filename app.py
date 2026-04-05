#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import random
import re
import string
import time
from contextlib import suppress
from typing import Any, Callable, Optional

from aiohttp import web, ClientSession, ClientTimeout, WSMsgType
from yandex_music import Client

LOGGER = logging.getLogger("yandex_music_bridge")


def env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def normalize_track_id(value: Any) -> Optional[str]:
    if not value:
        return None
    s = str(value)
    if s.isdigit():
        return s
    m = re.search(r"(?:track[:/])(\d+)", s)
    if m:
        return m.group(1)
    m = re.search(r"(\d+)(?:\D*$)", s)
    return m.group(1) if m else None


def build_cover_url(track: Any, size: str = "300x300") -> Optional[str]:
    cover_uri = getattr(track, "cover_uri", None)
    if not cover_uri:
        return None
    uri = cover_uri.replace("%%", size)
    return uri if uri.startswith("http") else f"https://{uri}"


def track_to_payload(track: Any, *, progress_ms: Optional[int] = None, paused: Optional[bool] = None,
                     context_type: Optional[str] = None, queue_id: Optional[str] = None,
                     source: Optional[str] = None) -> dict[str, Any]:
    artists_list = [a.name for a in (getattr(track, "artists", None) or []) if getattr(a, "name", None)]
    album_title = None
    albums = getattr(track, "albums", None)
    if albums:
        album_title = getattr(albums[0], "title", None)
    return {
        "title": getattr(track, "title", None),
        "artists": ", ".join(artists_list) if artists_list else None,
        "artists_list": artists_list,
        "album": album_title,
        "track_id": str(getattr(track, "id", None)) if getattr(track, "id", None) is not None else None,
        "cover": build_cover_url(track),
        "duration_ms": getattr(track, "duration_ms", None),
        "progress_ms": progress_ms,
        "paused": paused,
        "explicit": getattr(track, "explicit", None),
        "context_type": context_type,
        "queue_id": queue_id,
        "source": source,
        "timestamp": time.time(),
    }


class YnisonWatcher:
    def __init__(self, token: str, yaclient: Any, on_update: Callable[[Optional[dict[str, Any]]], None]):
        self._token = token
        self._client = yaclient
        self._on_update = on_update
        self._task: Optional[asyncio.Task] = None
        self._stopped = asyncio.Event()
        self._ws = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._runner())

    async def stop(self) -> None:
        self._stopped.set()
        if self._ws is not None:
            with suppress(Exception):
                await self._ws.close()
        if self._task:
            self._task.cancel()
            with suppress(Exception):
                await self._task
            self._task = None

    async def _runner(self) -> None:
        backoff = 2
        while not self._stopped.is_set():
            try:
                ok = await self._connect_once()
                if ok:
                    backoff = 2
                else:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)
            except asyncio.CancelledError:
                return
            except Exception as e:
                LOGGER.debug("Ynison loop error: %r", e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _connect_once(self) -> bool:
        device_id = "".join(random.choice(string.ascii_lowercase) for _ in range(16))
        device_info = {"app_name": "YandexMusicBridge", "type": 1}
        ws_proto = {
            "Ynison-Device-Id": device_id,
            "Ynison-Device-Info": json.dumps(device_info),
        }

        headers_redirect = {
            "Sec-WebSocket-Protocol": f"Bearer, v2, {json.dumps(ws_proto)}",
            "Origin": "http://music.yandex.ru",
            "Authorization": f"OAuth {self._token}",
        }

        timeout = ClientTimeout(total=None, connect=10)
        async with ClientSession(timeout=timeout) as session:
            try:
                async with session.ws_connect(
                    url="wss://ynison.music.yandex.ru/redirector.YnisonRedirectService/GetRedirectToYnison",
                    headers=headers_redirect,
                    timeout=10,
                ) as ws_redirect:
                    recv = await ws_redirect.receive(timeout=10)
                    data = json.loads(recv.data)
                    host = data.get("host")
                    ticket = data.get("redirect_ticket")
                    if not host or not ticket:
                        LOGGER.debug("Ynison redirect failed: %s", data)
                        return False
            except asyncio.TimeoutError:
                LOGGER.debug("Ynison redirect timeout")
                return False

            new_ws_proto = dict(ws_proto)
            new_ws_proto["Ynison-Redirect-Ticket"] = ticket
            headers = {
                "Sec-WebSocket-Protocol": f"Bearer, v2, {json.dumps(new_ws_proto)}",
                "Origin": "http://music.yandex.ru",
                "Authorization": f"OAuth {self._token}",
            }
            url = f"wss://{host}/ynison_state.YnisonStateService/PutYnisonState"
            async with session.ws_connect(url=url, headers=headers, timeout=10) as ws:
                self._ws = ws
                LOGGER.info("Ynison connected: host=%s device_id=%s", host, device_id)
                bootstrap = {
                    "update_full_state": {
                        "player_state": {
                            "player_queue": {
                                "current_playable_index": -1,
                                "entity_id": "",
                                "entity_type": "VARIOUS",
                                "playable_list": [],
                                "options": {"repeat_mode": "NONE"},
                                "entity_context": "BASED_ON_ENTITY_BY_DEFAULT",
                                "version": {"device_id": device_id, "version": 0, "timestamp_ms": 0},
                                "from_optional": "",
                            },
                            "status": {
                                "duration_ms": 0,
                                "paused": True,
                                "playback_speed": 1,
                                "progress_ms": 0,
                                "version": {"device_id": device_id, "version": 0, "timestamp_ms": 0},
                            },
                        },
                        "device": {
                            "capabilities": {
                                "can_be_player": True,
                                "can_be_remote_controller": False,
                                "volume_granularity": 16,
                            },
                            "info": {
                                "device_id": device_id,
                                "type": "WEB",
                                "title": "Yandex Music Bridge",
                                "app_name": "YandexMusicBridge",
                            },
                            "volume_info": {"volume": 0},
                            "is_shadow": True,
                        },
                        "is_currently_active": False,
                    },
                    "rid": device_id,
                    "player_action_timestamp_ms": 0,
                    "activity_interception_type": "DO_NOT_INTERCEPT_BY_DEFAULT",
                }
                await ws.send_str(json.dumps(bootstrap))

                async def ping_loop() -> None:
                    try:
                        while True:
                            await asyncio.sleep(25)
                            await ws.ping()
                    except Exception:
                        return

                ping_task = asyncio.create_task(ping_loop())
                try:
                    while not self._stopped.is_set():
                        try:
                            msg = await ws.receive(timeout=60)
                        except asyncio.TimeoutError:
                            continue
                        if msg.type == WSMsgType.TEXT:
                            try:
                                await self._handle_state(json.loads(msg.data))
                            except Exception as e:
                                LOGGER.debug("Ynison parse error: %r", e)
                        elif msg.type in (WSMsgType.CLOSED, WSMsgType.CLOSING, WSMsgType.ERROR):
                            return False
                finally:
                    ping_task.cancel()
                    self._ws = None
        return True

    async def _handle_state(self, data: dict[str, Any]) -> None:
        ps = data.get("player_state") or {}
        q = ps.get("player_queue") or {}
        status = ps.get("status") or {}
        idx = q.get("current_playable_index", -1)
        lst = q.get("playable_list") or []
        playable_id = None
        if isinstance(idx, int) and 0 <= idx < len(lst):
            playable = lst[idx] or {}
            playable_id = playable.get("playable_id") or playable.get("id")
        track_id = normalize_track_id(playable_id)
        if not track_id:
            self._on_update(None)
            return
        loop = asyncio.get_running_loop()
        tracks = await loop.run_in_executor(None, lambda: self._client.tracks(track_id))
        track = tracks[0] if isinstance(tracks, list) and tracks else None
        if track is None:
            self._on_update(None)
            return
        update = track_to_payload(
            track,
            progress_ms=status.get("progress_ms"),
            paused=status.get("paused"),
            context_type=q.get("entity_type"),
            queue_id=q.get("entity_id"),
            source="ynison",
        )
        self._on_update(update)


class YandexMusicBridge:
    def __init__(self) -> None:
        self.token = os.environ["YM_TOKEN"]
        self.language = os.getenv("YM_LANGUAGE", "ru")
        self.api_key = os.getenv("YM_API_KEY")
        self.bind = os.getenv("YM_BIND", "0.0.0.0")
        self.port = int(os.getenv("YM_PORT", "9980"))
        self.enable_ynison = env_bool("YM_ENABLE_YNISON", True)
        self.push_ttl = float(os.getenv("YM_PUSH_TTL", "45"))
        self.queue_cache_ttl = float(os.getenv("YM_QUEUE_CACHE_TTL", "15"))
        self.restart_cooldown = float(os.getenv("YM_YNISON_RESTART_COOLDOWN", "30"))
        self.client: Any = None
        self.ynison: Optional[YnisonWatcher] = None
        self._started = False
        self._last_push: Optional[dict[str, Any]] = None
        self._last_push_ts: float = 0.0
        self._last_queue: Optional[dict[str, Any]] = None
        self._last_queue_ts: float = 0.0
        self._last_data: Optional[dict[str, Any]] = None
        self._last_data_source: Optional[str] = None
        self._last_error: Optional[str] = None
        self._queue_lock = asyncio.Lock()
        self._restart_lock = asyncio.Lock()
        self._background_task: Optional[asyncio.Task] = None
        self._ynison_expected_end_ts: float = 0.0
        self._ynison_last_progress_ts: float = 0.0
        self._ynison_last_progress_ms: Optional[float] = None
        self._ynison_last_track_id: Optional[str] = None
        self._ynison_stale_hits: int = 0
        self._ynison_last_reconnect_ts: float = 0.0

    async def start(self) -> None:
        if self._started:
            return
        loop = asyncio.get_running_loop()
        self.client = await loop.run_in_executor(None, self._build_client)
        LOGGER.info("Yandex Music client initialized")
        if self.enable_ynison:
            self.ynison = YnisonWatcher(self.token, self.client, self._handle_push_update)
            self.ynison.start()
        self._background_task = asyncio.create_task(self._background_loop())
        self._started = True

    async def stop(self) -> None:
        if self._background_task:
            self._background_task.cancel()
            with suppress(Exception):
                await self._background_task
            self._background_task = None
        if self.ynison:
            with suppress(Exception):
                await self.ynison.stop()
            self.ynison = None
        self._started = False

    def _build_client(self) -> Any:
        client = Client(self.token)
        client.init()
        return client

    def _handle_push_update(self, data: Optional[dict[str, Any]]) -> None:
        self._last_push_ts = time.monotonic()
        self._update_ynison_watchdog(data)
        if data is None:
            LOGGER.debug("Ynison push: clear state")
            return
        LOGGER.debug("Ynison push: %s - %s", data.get("artists"), data.get("title"))
        self._last_push = dict(data)
        self._last_data = dict(data)
        self._last_data_source = "ynison"

    def _update_ynison_watchdog(self, data: Optional[dict[str, Any]]) -> None:
        now = time.monotonic()
        if not data:
            self._ynison_expected_end_ts = 0.0
            self._ynison_last_progress_ts = now
            self._ynison_last_progress_ms = None
            self._ynison_last_track_id = None
            self._ynison_stale_hits = 0
            return
        track_id = str(data.get("track_id") or "")
        paused = bool(data.get("paused"))
        progress_ms = data.get("progress_ms")
        duration_ms = data.get("duration_ms")
        try:
            progress_ms_f = float(progress_ms) if progress_ms is not None else None
        except Exception:
            progress_ms_f = None
        try:
            duration_ms_f = float(duration_ms) if duration_ms is not None else None
        except Exception:
            duration_ms_f = None
        prev_track_id = self._ynison_last_track_id
        prev_progress_ms = self._ynison_last_progress_ms
        if paused:
            self._ynison_stale_hits = 0
        elif track_id and track_id == prev_track_id and progress_ms_f is not None and prev_progress_ms is not None:
            if progress_ms_f <= prev_progress_ms:
                self._ynison_stale_hits += 1
            else:
                self._ynison_stale_hits = 0
        else:
            self._ynison_stale_hits = 0
        if not paused and duration_ms_f and progress_ms_f is not None and duration_ms_f > progress_ms_f:
            self._ynison_expected_end_ts = now + ((duration_ms_f - progress_ms_f) / 1000.0)
        else:
            self._ynison_expected_end_ts = 0.0
        self._ynison_last_track_id = track_id or None
        self._ynison_last_progress_ms = progress_ms_f
        self._ynison_last_progress_ts = now

    def _get_ynison_stale_reason(self) -> Optional[str]:
        if not self.enable_ynison or self.ynison is None or self._last_push is None:
            return None
        now = time.monotonic()
        push_age = now - self._last_push_ts if self._last_push_ts else None
        if push_age is None:
            return None
        if self._ynison_last_reconnect_ts and (now - self._ynison_last_reconnect_ts) < self.restart_cooldown:
            return None
        if self._ynison_expected_end_ts > 0 and now > (self._ynison_expected_end_ts + max(20.0, self.queue_cache_ttl * 2.0)):
            overdue = now - self._ynison_expected_end_ts
            return f"track-overdue:{overdue:.1f}s"
        if self._ynison_stale_hits >= 2 and push_age > 8.0:
            return f"stagnant-progress:hits={self._ynison_stale_hits}:push_age={push_age:.1f}s"
        return None

    async def _restart_ynison(self, reason: str) -> None:
        if not self.enable_ynison:
            return
        async with self._restart_lock:
            now = time.monotonic()
            if self._ynison_last_reconnect_ts and (now - self._ynison_last_reconnect_ts) < self.restart_cooldown:
                return
            self._ynison_last_reconnect_ts = now
            LOGGER.warning("Restarting Ynison watcher: %s", reason)
            if self.ynison:
                with suppress(Exception):
                    await self.ynison.stop()
            self.ynison = YnisonWatcher(self.token, self.client, self._handle_push_update)
            self.ynison.start()

    async def _background_loop(self) -> None:
        while True:
            await asyncio.sleep(max(5.0, min(self.queue_cache_ttl, 15.0)))
            reason = self._get_ynison_stale_reason()
            if reason:
                await self._restart_ynison(reason)

    def _fetch_now_playing_pull_sync(self) -> Optional[dict[str, Any]]:
        try:
            queues = self.client.queues_list()
            if not queues:
                return None
            for qi in list(queues):
                qid = getattr(qi, "id", None) or getattr(qi, "queue_id", None)
                q = None
                if hasattr(qi, "fetch_queue"):
                    with suppress(Exception):
                        q = qi.fetch_queue()
                if q is None and qid:
                    with suppress(Exception):
                        q = self.client.queue(qid)
                if not q:
                    continue
                current_index = getattr(q, "current_index", -1)
                if current_index is None or current_index < 0:
                    continue
                try:
                    tid = q.get_current_track()
                except Exception:
                    tid = None
                if not tid:
                    continue
                track_id = getattr(tid, "id", None) or getattr(tid, "track_id", None) or tid
                if not track_id:
                    continue
                tr_list = self.client.tracks(track_id)
                track = tr_list[0] if isinstance(tr_list, list) and tr_list else (tr_list if tr_list else None)
                if not track:
                    continue
                context_type = getattr(getattr(q, "context", None), "type", None)
                return track_to_payload(track, context_type=context_type, queue_id=str(getattr(q, "id", None) or qid), source="queues")
            return None
        except Exception as e:
            self._last_error = str(e)
            LOGGER.debug("queues pull failed: %r", e)
            return None

    async def _fetch_queue_if_needed(self) -> Optional[dict[str, Any]]:
        now = time.monotonic()
        if self._last_queue is not None and (now - self._last_queue_ts) <= self.queue_cache_ttl:
            return self._last_queue
        async with self._queue_lock:
            now = time.monotonic()
            if self._last_queue is not None and (now - self._last_queue_ts) <= self.queue_cache_ttl:
                return self._last_queue
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, self._fetch_now_playing_pull_sync)
            self._last_queue = data
            self._last_queue_ts = time.monotonic()
            if data is not None:
                self._last_data = dict(data)
                self._last_data_source = "queues"
            return data

    async def get_snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        push_age = (now - self._last_push_ts) if self._last_push_ts else None
        stale_reason = self._get_ynison_stale_reason()
        if stale_reason:
            asyncio.create_task(self._restart_ynison(stale_reason))
        if self.enable_ynison and self._last_push is not None and push_age is not None and push_age <= self.push_ttl and not stale_reason:
            return {
                "ok": True,
                "source": "ynison",
                "fresh": True,
                "push_age": push_age,
                "data": self._last_push,
            }
        queue_data = await self._fetch_queue_if_needed()
        if queue_data is not None:
            return {
                "ok": True,
                "source": "queues",
                "fresh": True,
                "push_age": push_age,
                "data": queue_data,
            }
        if self._last_push is not None and push_age is not None and push_age <= (self.push_ttl * 2.0):
            return {
                "ok": True,
                "source": "ynison-cache",
                "fresh": False,
                "push_age": push_age,
                "data": self._last_push,
            }
        return {
            "ok": True,
            "source": "none",
            "fresh": False,
            "push_age": push_age,
            "data": None,
        }

    async def get_health(self) -> dict[str, Any]:
        snap = await self.get_snapshot()
        return {
            "ok": True,
            "service": "yandex-music-bridge",
            "ynison_enabled": self.enable_ynison,
            "ynison_connected": self.ynison is not None,
            "last_push_age": (time.monotonic() - self._last_push_ts) if self._last_push_ts else None,
            "last_queue_age": (time.monotonic() - self._last_queue_ts) if self._last_queue_ts else None,
            "last_data_source": self._last_data_source,
            "stale_reason": self._get_ynison_stale_reason(),
            "last_error": self._last_error,
            "snapshot": snap,
        }


def require_api_key(handler):
    async def wrapped(request: web.Request):
        bridge: YandexMusicBridge = request.app["bridge"]
        if bridge.api_key:
            supplied = request.headers.get("X-API-Key") or request.query.get("api_key")
            if supplied != bridge.api_key:
                return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        return await handler(request)
    return wrapped


@require_api_key
async def now_playing_handler(request: web.Request) -> web.Response:
    bridge: YandexMusicBridge = request.app["bridge"]
    snap = await bridge.get_snapshot()
    return web.json_response(snap)


@require_api_key
async def health_handler(request: web.Request) -> web.Response:
    bridge: YandexMusicBridge = request.app["bridge"]
    data = await bridge.get_health()
    return web.json_response(data)


async def on_startup(app: web.Application) -> None:
    await app["bridge"].start()


async def on_cleanup(app: web.Application) -> None:
    await app["bridge"].stop()



def build_app() -> web.Application:
    bridge = YandexMusicBridge()
    app = web.Application()
    app["bridge"] = bridge
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/now-playing", now_playing_handler)
    return app


if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, os.getenv("YM_LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    app = build_app()
    web.run_app(app, host=os.getenv("YM_BIND", "0.0.0.0"), port=int(os.getenv("YM_PORT", "9980")))
