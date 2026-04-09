#!/usr/bin/env python3
import asyncio
import ipaddress
import json
import logging
import os
import random
import re
import ssl
import string
import time
import uuid
from contextlib import suppress
from typing import Any, Callable, Optional

from aiohttp import web, ClientSession, ClientTimeout, WSMsgType, ClientWebSocketResponse
from yandex_music import Client
from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf

LOGGER = logging.getLogger("yandex_audio_bridge")


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


def build_cover_url_from_uri(uri: Optional[str], size: str = "300x300") -> Optional[str]:
    if not uri:
        return None
    uri = uri.replace("%%", size)
    return uri if uri.startswith("http") else f"https://{uri}"


def build_cover_url(track: Any, size: str = "300x300") -> Optional[str]:
    return build_cover_url_from_uri(getattr(track, "cover_uri", None), size)


def track_to_payload(track: Any, *, progress_ms: Optional[int] = None, paused: Optional[bool] = None,
                     context_type: Optional[str] = None, queue_id: Optional[str] = None,
                     source: Optional[str] = None, player_id: Optional[str] = None,
                     device_id: Optional[str] = None, device_name: Optional[str] = None,
                     platform: Optional[str] = None, host: Optional[str] = None) -> dict[str, Any]:
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
        "player_id": player_id,
        "device_id": device_id,
        "device_name": device_name,
        "platform": platform,
        "host": host,
        "timestamp": time.time(),
    }


def station_state_to_payload(state: dict[str, Any], device: dict[str, Any]) -> Optional[dict[str, Any]]:
    player = state.get("playerState") or {}
    title = player.get("title")
    subtitle = player.get("subtitle")
    if not title:
        return None
    progress_s = player.get("progress")
    duration_s = player.get("duration")
    track_id = normalize_track_id(player.get("id") or player.get("entityInfo", {}).get("id"))
    return {
        "title": title,
        "artists": subtitle,
        "artists_list": [subtitle] if subtitle else [],
        "album": player.get("playlistDescription") or None,
        "track_id": track_id,
        "cover": build_cover_url_from_uri((player.get("extra") or {}).get("coverURI")),
        "duration_ms": int(duration_s * 1000) if isinstance(duration_s, (int, float)) else None,
        "progress_ms": int(progress_s * 1000) if isinstance(progress_s, (int, float)) else None,
        "paused": not bool(state.get("playing")),
        "explicit": None,
        "context_type": (player.get("entityInfo") or {}).get("type") or player.get("playlistType"),
        "queue_id": None,
        "source": "station-local",
        "player_id": f"station:{device.get('device_id')}",
        "device_id": device.get("device_id"),
        "device_name": device.get("name"),
        "platform": device.get("platform"),
        "host": device.get("host"),
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
        device_info = {"app_name": "YandexAudioBridge", "type": 1}
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
                                "title": "Yandex Audio Bridge",
                                "app_name": "YandexAudioBridge",
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
            player_id="music:account",
            device_id="music-account",
            device_name="Yandex Music",
            platform="ynison",
        )
        self._on_update(update)


class LocalStationConnection:
    def __init__(self, token: str, device: dict[str, Any], on_update: Callable[[str, Optional[dict[str, Any]]], None]):
        self.token = token
        self.device = device
        self.on_update = on_update
        self.device_token: Optional[str] = None
        self.url: Optional[str] = None
        self.ws: Optional[ClientWebSocketResponse] = None
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self.player_id = f"station:{device['device_id']}"

    async def _get_device_token(self, session: ClientSession) -> str:
        params = {"device_id": self.device["device_id"], "platform": self.device["platform"]}
        headers = {"Authorization": f"OAuth {self.token}"}
        async with session.get("https://quasar.yandex.net/glagol/token", params=params, headers=headers) as resp:
            text = await resp.text()
            data = json.loads(text)
            if data.get("status") != "ok":
                raise RuntimeError(f"glagol token failed: {data}")
            return data["token"]

    def start(self) -> None:
        self.url = f"wss://{self.device['host']}:{self.device['port']}"
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._runner())

    async def stop(self) -> None:
        self._stop.set()
        if self.ws:
            with suppress(Exception):
                await self.ws.close()
        if self._task:
            self._task.cancel()
            with suppress(Exception):
                await self._task
            self._task = None
        self.on_update(self.player_id, None)

    async def _runner(self) -> None:
        fails = 0
        while not self._stop.is_set():
            try:
                await self._connect_once()
                fails = 0
            except asyncio.CancelledError:
                return
            except Exception as e:
                fails += 1
                delay = min(30 * max(fails - 1, 0), 300)
                LOGGER.debug("Local station %s reconnect in %ss after %r", self.device.get("name"), delay, e)
                self.on_update(self.player_id, None)
                await asyncio.sleep(delay)

    async def _connect_once(self) -> None:
        timeout = ClientTimeout(total=None, connect=10)
        ssl_ctx = False
        async with ClientSession(timeout=timeout) as session:
            if not self.device_token:
                self.device_token = await self._get_device_token(session)
            self.ws = await session.ws_connect(self.url, heartbeat=55, ssl=ssl_ctx)
            await self._send_command("softwareVersion")
            async for msg in self.ws:
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    state = data.get("state") or {}
                    payload = station_state_to_payload(state, self.device)
                    self.on_update(self.player_id, payload)
                elif msg.type in (WSMsgType.CLOSED, WSMsgType.CLOSING, WSMsgType.ERROR):
                    break
        self.ws = None
        self.device_token = None
        raise RuntimeError("station websocket disconnected")

    async def _send_command(self, command: str = "ping") -> None:
        if not self.ws:
            return
        try:
            await self.ws.send_json(
                {
                    "conversationToken": self.device_token,
                    "id": str(uuid.uuid4()),
                    "payload": {"command": command},
                    "sentTime": int(round(time.time() * 1000)),
                }
            )
        except Exception:
            pass


class LocalStationDiscovery:
    def __init__(self, on_add: Callable[[dict[str, Any]], None]):
        self.on_add = on_add
        self.zc: Optional[Zeroconf] = None
        self.browser = None

    def start(self) -> None:
        self.zc = Zeroconf()
        self.browser = ServiceBrowser(self.zc, "_yandexio._tcp.local.", handlers=[self._handler])

    def stop(self) -> None:
        if self.browser:
            self.browser.cancel()
        if self.zc:
            self.zc.close()

    def _handler(self, zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange):
        if state_change not in (ServiceStateChange.Added, ServiceStateChange.Updated):
            return
        try:
            info = zeroconf.get_service_info(service_type, name)
            if not info or not info.addresses:
                return
            properties = {
                k.decode(): v.decode() if isinstance(v, bytes) else v
                for k, v in info.properties.items()
            }
            device = {
                "device_id": properties["deviceId"],
                "platform": properties.get("platform", "unknown"),
                "host": str(ipaddress.ip_address(info.addresses[0])),
                "port": info.port,
                "name": properties.get("name") or properties.get("deviceName") or properties["deviceId"],
            }
            self.on_add(device)
        except Exception as e:
            LOGGER.debug("Zeroconf parse error: %r", e)




def parse_stereo_groups_env(value: str) -> dict[str, frozenset[str]]:
    groups: dict[str, frozenset[str]] = {}
    if not value:
        return groups
    for raw_group in value.split(';'):
        members = [x.strip() for x in raw_group.replace(',', '|').split('|') if x.strip()]
        if len(members) < 2:
            continue
        frozen = frozenset(members)
        for member in members:
            groups[member] = frozen
    return groups


def _station_group_signature(payload: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(payload.get("track_id") or ""),
        str(payload.get("title") or ""),
        str(payload.get("artists") or ""),
        str(payload.get("album") or ""),
    )


def _merge_station_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    sorted_group = sorted(group, key=lambda p: (bool(p.get("paused")), -(p.get("duration_ms") or 0), str(p.get("player_id") or "")))
    primary = dict(sorted_group[0])
    ids = sorted({str(p.get("device_id") or p.get("player_id") or "") for p in group if (p.get("device_id") or p.get("player_id"))})
    names = [str(p.get("device_name") or p.get("device_id") or p.get("player_id") or "") for p in group]
    hosts = [str(p.get("host") or "") for p in group if p.get("host")]
    primary["player_id"] = f"stereo:{'|'.join(ids)}" if ids else str(primary.get("player_id") or "stereo:unknown")
    primary["device_id"] = primary["player_id"]
    primary["device_name"] = f"Stereo Pair: {' + '.join(names[:2])}" if names else str(primary.get("device_name") or "Stereo Pair")
    primary["platform"] = f"{primary.get('platform') or 'station-local'}+stereo"
    if hosts:
        primary["host"] = ",".join(hosts)
    primary["stereo_pair"] = True
    primary["stereo_members"] = ids
    primary["timestamp"] = max(float(p.get("timestamp") or 0.0) for p in group)
    if primary.get("duration_ms") is not None and primary.get("progress_ms") is not None:
        try:
            primary["progress_ms"] = min(int(primary["progress_ms"]), int(primary["duration_ms"]))
        except Exception:
            pass
    return primary


class YandexAudioBridge:
    def __init__(self, token: str):
        self.token = token
        self.client: Any = None
        self.enable_ynison = env_bool("YM_ENABLE_YNISON", True)
        self.enable_music = env_bool("YM_ENABLE_MUSIC", True)
        self.enable_stations = env_bool("YM_ENABLE_STATIONS", True)
        self.push_ttl = float(os.getenv("YM_PUSH_TTL", "45"))
        self.player_ttl = float(os.getenv("YM_PLAYER_TTL", "180"))
        self.queue_cache_ttl = float(os.getenv("YM_QUEUE_CACHE_TTL", "3"))
        self._queue_lock = asyncio.Lock()
        self._last_queue_ts = 0.0
        self._last_error: Optional[str] = None
        self._last_push: Optional[dict[str, Any]] = None
        self._last_push_ts: float = 0.0
        self.ynison: Optional[YnisonWatcher] = None
        self.discovery: Optional[LocalStationDiscovery] = None
        self.station_connections: dict[str, LocalStationConnection] = {}
        self.players: dict[str, dict[str, Any]] = {}
        self.stereo_groups = parse_stereo_groups_env(os.getenv("YM_STEREO_GROUPS", "").strip())
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._started = False

    def _build_client(self) -> Any:
        client = Client(self.token)
        client.init()
        return client

    async def start(self) -> None:
        if self._started:
            return
        self.loop = asyncio.get_running_loop()
        if self.enable_music:
            loop = self.loop
            self.client = await loop.run_in_executor(None, self._build_client)
            LOGGER.info("Yandex Music client initialized")
            if self.enable_ynison:
                self.ynison = YnisonWatcher(self.token, self.client, self._handle_music_push)
                self.ynison.start()
        if self.enable_stations:
            self.discovery = LocalStationDiscovery(self._handle_station_found)
            self.discovery.start()
            LOGGER.info("Local Yandex Station discovery started")
        self._started = True

    async def stop(self) -> None:
        if self.ynison:
            with suppress(Exception):
                await self.ynison.stop()
        for conn in list(self.station_connections.values()):
            with suppress(Exception):
                await conn.stop()
        if self.discovery:
            with suppress(Exception):
                self.discovery.stop()
        self._started = False

    def _set_player(self, player_id: str, payload: Optional[dict[str, Any]]) -> None:
        if payload is None:
            if player_id in self.players:
                self.players[player_id]["last_seen"] = time.monotonic()
                self.players[player_id]["stale"] = True
            return
        data = dict(payload)
        data["last_seen"] = time.monotonic()
        data["stale"] = False
        self.players[player_id] = data

    def _handle_music_push(self, data: Optional[dict[str, Any]]) -> None:
        self._last_push_ts = time.monotonic()
        self._last_push = dict(data) if data else None
        if data:
            self._set_player(data.get("player_id") or "music:account", data)

    def _handle_station_found(self, device: dict[str, Any]) -> None:
        if self.loop is None:
            LOGGER.debug("Ignoring discovered station before event loop is ready: %s", device.get("device_id"))
            return
        self.loop.call_soon_threadsafe(self._handle_station_found_on_loop, dict(device))

    def _handle_station_found_on_loop(self, device: dict[str, Any]) -> None:
        player_id = f"station:{device['device_id']}"
        existing = self.station_connections.get(player_id)
        if existing is not None:
            existing.device.update(device)
            return
        LOGGER.info("Discovered Yandex Station %s at %s:%s", device.get("name"), device.get("host"), device.get("port"))
        conn = LocalStationConnection(self.token, device, self._set_player)
        self.station_connections[player_id] = conn
        conn.start()

    def _fetch_music_players_sync(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        try:
            queues = self.client.queues_list()
            if not queues:
                return []
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
                payload = track_to_payload(
                    track,
                    context_type=context_type,
                    queue_id=str(getattr(q, "id", None) or qid),
                    source="queues",
                    player_id=f"queue:{getattr(q, 'id', None) or qid}",
                    device_id=f"queue:{getattr(q, 'id', None) or qid}",
                    device_name=f"Yandex Queue {getattr(q, 'id', None) or qid}",
                    platform="yandex-music-queue",
                )
                results.append(payload)
            return results
        except Exception as e:
            self._last_error = str(e)
            LOGGER.debug("queues pull failed: %r", e)
            return []

    async def refresh_music_players(self) -> None:
        if not self.enable_music or self.client is None:
            return
        now = time.monotonic()
        if (now - self._last_queue_ts) <= self.queue_cache_ttl:
            return
        async with self._queue_lock:
            now = time.monotonic()
            if (now - self._last_queue_ts) <= self.queue_cache_ttl:
                return
            loop = asyncio.get_running_loop()
            payloads = await loop.run_in_executor(None, self._fetch_music_players_sync)
            for payload in payloads:
                self._set_player(payload.get("player_id") or payload.get("device_id") or str(uuid.uuid4()), payload)
            self._last_queue_ts = time.monotonic()

    def _prune_players(self) -> None:
        now = time.monotonic()
        remove: list[str] = []
        for player_id, payload in self.players.items():
            last_seen = float(payload.get("last_seen") or 0.0)
            if not last_seen:
                continue
            if (now - last_seen) > self.player_ttl:
                remove.append(player_id)
        for player_id in remove:
            self.players.pop(player_id, None)


    def _collapse_stereo_pairs(self, players: list[dict[str, Any]]) -> list[dict[str, Any]]:
        station_players = [p for p in players if p.get("source") == "station-local"]
        if len(station_players) < 2:
            return players

        non_station = [p for p in players if p.get("source") != "station-local"]
        consumed_player_ids: set[str] = set()
        merged_station: list[dict[str, Any]] = []

        # 1) Explicit groups from env YM_STEREO_GROUPS=device1|device2;device3|device4
        grouped_by_manual: dict[frozenset[str], list[dict[str, Any]]] = {}
        for payload in station_players:
            device_id = str(payload.get("device_id") or "")
            group = self.stereo_groups.get(device_id)
            if group is not None:
                grouped_by_manual.setdefault(group, []).append(payload)

        for _, items in grouped_by_manual.items():
            if len(items) >= 2:
                merged_station.append(_merge_station_group(items))
                for item in items:
                    consumed_player_ids.add(str(item.get("player_id") or ""))

        remaining = [p for p in station_players if str(p.get("player_id") or "") not in consumed_player_ids]
        if len(remaining) < 2:
            return non_station + merged_station + remaining

        # 2) Heuristic merge. Much looser than before so active stereo pairs do not split
        #    just because reported progress differs between left/right speakers.
        groups: list[list[dict[str, Any]]] = []
        consumed: set[int] = set()
        for idx, payload in enumerate(remaining):
            if idx in consumed:
                continue
            consumed.add(idx)
            group = [payload]
            sig = _station_group_signature(payload)
            prog = payload.get("progress_ms")
            for jdx in range(idx + 1, len(remaining)):
                if jdx in consumed:
                    continue
                other = remaining[jdx]
                if _station_group_signature(other) != sig:
                    continue
                other_prog = other.get("progress_ms")
                if prog is not None and other_prog is not None:
                    try:
                        if abs(int(prog) - int(other_prog)) > 15000:
                            continue
                    except Exception:
                        pass
                consumed.add(jdx)
                group.append(other)
            groups.append(group)

        for group in groups:
            if len(group) >= 2:
                merged_station.append(_merge_station_group(group))
            else:
                merged_station.append(group[0])

        return non_station + merged_station

    async def get_players_snapshot(self) -> dict[str, Any]:
        await self.refresh_music_players()
        self._prune_players()
        players = list(self.players.values())
        players = self._collapse_stereo_pairs(players)
        players.sort(key=lambda x: (bool(x.get("paused")), -(x.get("timestamp") or 0)))
        primary = players[0] if players else None
        return {
            "ok": True,
            "source": "bridge-v3",
            "fresh": True,
            "players_count": len(players),
            "primary": primary,
            "data": players,
        }

    async def get_snapshot(self) -> dict[str, Any]:
        snap = await self.get_players_snapshot()
        return {
            "ok": True,
            "source": snap["source"],
            "fresh": snap["fresh"],
            "data": snap["primary"],
        }

    async def get_health(self) -> dict[str, Any]:
        players = await self.get_players_snapshot()
        return {
            "ok": True,
            "service": "yandex-audio-bridge-v3",
            "music_enabled": self.enable_music,
            "stations_enabled": self.enable_stations,
            "ynison_enabled": self.enable_ynison,
            "ynison_connected": self.ynison is not None,
            "known_station_connections": len(self.station_connections),
            "players_count": players.get("players_count", 0),
            "last_push_age": (time.monotonic() - self._last_push_ts) if self._last_push_ts else None,
            "last_queue_age": (time.monotonic() - self._last_queue_ts) if self._last_queue_ts else None,
            "last_error": self._last_error,
        }


async def create_app() -> web.Application:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = os.getenv("YM_TOKEN", "").strip()
    if token == "":
        raise RuntimeError("YM_TOKEN is required")
    api_key = os.getenv("YM_API_KEY", "").strip()
    bridge = YandexAudioBridge(token)
    app = web.Application()
    app["bridge"] = bridge
    app["api_key"] = api_key

    @web.middleware
    async def auth_middleware(request: web.Request, handler):
        required = request.app["api_key"]
        if required:
            got = request.headers.get("X-API-Key", "")
            if got != required:
                return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        return await handler(request)

    app.middlewares.append(auth_middleware)

    async def on_startup(app: web.Application):
        await app["bridge"].start()

    async def on_cleanup(app: web.Application):
        await app["bridge"].stop()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    async def health(request: web.Request):
        return web.json_response(await app["bridge"].get_health())

    async def now_playing(request: web.Request):
        return web.json_response(await app["bridge"].get_snapshot())

    async def players(request: web.Request):
        return web.json_response(await app["bridge"].get_players_snapshot())

    app.router.add_get("/health", health)
    app.router.add_get("/now-playing", now_playing)
    app.router.add_get("/players", players)
    return app


if __name__ == "__main__":
    port = int(os.getenv("YM_PORT", "9980"))
    web.run_app(create_app(), host="0.0.0.0", port=port)
