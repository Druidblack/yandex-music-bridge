"""Independent Yandex Music Desktop instances using the CDP implementation.

The CDP client and JS observers are adapted from the MIT-licensed
Yandex Music Desktop Home Assistant integration by Druidblack (2026).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from aiohttp import ClientSession, ClientTimeout

from desktop_cdp import CDPError, YandexMusicCDP

LOGGER = logging.getLogger("yandex_audio_bridge.desktop")


@dataclass(frozen=True)
class DesktopConfig:
    name: str
    host: str
    port: int

    @property
    def player_id(self) -> str:
        # Host and port, not display name, determine identity across restarts.
        return f"desktop:{self.host.lower()}:{self.port}"


def parse_desktop_players(value: str) -> list[DesktopConfig]:
    """Parse 'Name@host:port,Name 2@host:port' (IPv6 in brackets)."""
    configs: list[DesktopConfig] = []
    seen: set[str] = set()
    for raw in value.split(","):
        entry = raw.strip()
        if not entry:
            continue
        if "@" in entry:
            name, address = entry.rsplit("@", 1)
            name = name.strip()
        else:
            name, address = "", entry
        address = address.strip()
        if not address or ":" not in address:
            raise ValueError(f"Invalid YM_DESKTOP_PLAYERS entry {entry!r}: expected Name@host:port")
        host_text, port_text = address.rsplit(":", 1)
        host_text = host_text.strip()
        if host_text.startswith("[") and host_text.endswith("]"):
            host = host_text[1:-1]
            try:
                if ipaddress.ip_address(host).version != 6:
                    raise ValueError("Only IPv6 addresses may use brackets")
            except ValueError as exc:
                raise ValueError(f"Invalid IPv6 host in YM_DESKTOP_PLAYERS: {entry!r}") from exc
        else:
            host = host_text
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", host):
                raise ValueError(f"Invalid host in YM_DESKTOP_PLAYERS: {entry!r}")
        if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            raise ValueError(f"Invalid port in YM_DESKTOP_PLAYERS: {entry!r}")
        port = int(port_text)
        config = DesktopConfig(name=name or f"Yandex Music {host}:{port}", host=host, port=port)
        if config.player_id in seen:
            raise ValueError(f"Duplicate Yandex Music Desktop endpoint: {host}:{port}")
        seen.add(config.player_id)
        configs.append(config)
    return configs


def _track_id_from_url(url: object) -> str | None:
    """Extract only a real track ID; an album-only fallback is not a track."""
    if not isinstance(url, str) or not url:
        return None
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in {
            "music.yandex.ru", "music.yandex.com", "music.yandex.by",
            "music.yandex.kz", "music.yandex.uz",
        }:
            return None
        match = re.search(r"/track/(\d+)(?:/|$)", parts.path)
        if match:
            return match.group(1)
        query = parse_qs(parts.query)
        track_id = (query.get("trackId") or query.get("track_id") or [None])[0]
        return str(track_id) if track_id and str(track_id).isdigit() else None
    except ValueError:
        return None


def desktop_state_to_payload(state: dict, config: DesktopConfig) -> dict | None:
    """Translate normalized CDP state into the bridge's /players schema."""
    if not state.get("online") or not state.get("title"):
        return None
    duration = state.get("duration")
    progress = state.get("position")
    artist = state.get("artist")
    playing = state.get("playing")
    return {
        "title": state.get("title"),
        "artists": artist,
        "artists_list": [a.strip() for a in artist.split(",") if a.strip()] if isinstance(artist, str) else [],
        "album": None,
        "track_id": _track_id_from_url(state.get("track_url")),
        "track_url": state.get("track_url"),
        "cover": state.get("cover_url"),
        "duration_ms": round(duration * 1000) if isinstance(duration, (int, float)) and duration >= 0 else None,
        "progress_ms": round(progress * 1000) if isinstance(progress, (int, float)) and progress >= 0 else None,
        "paused": not playing if isinstance(playing, bool) else None,
        "explicit": None,
        "context_type": "yandex-music-desktop",
        "queue_id": None,
        "source": "desktop-cdp",
        "player_id": config.player_id,
        "device_id": config.player_id,
        "device_name": config.name,
        "platform": "yandex-music-desktop",
        "host": config.host,
        "port": config.port,
        "online": True,
        "liked": state.get("liked"),
        "shuffle": state.get("shuffle"),
        "repeat": state.get("repeat"),
        "volume": state.get("volume"),
        "muted": state.get("muted"),
        "timestamp": time.time(),
    }


class DesktopPlayerWatcher:
    """Always reconnect one configured desktop, independent of all other peers."""

    def __init__(
        self, config: DesktopConfig,
        on_update: Callable[[str, dict | None], None],
        *, poll_interval: float = 20.0,
        retry_interval: float = 10.0,
    ) -> None:
        self.config = config
        self.on_update = on_update
        self.poll_interval = poll_interval
        self.retry_interval = retry_interval
        self._task: asyncio.Task | None = None
        self._cdp: YandexMusicCDP | None = None
        self._stopping = False
        self.online = False
        self.connected = False
        self.last_error: str | None = None
        self.last_update: float | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name=f"desktop_{self.config.player_id}")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._set_state(None)

    def _set_state(self, state: dict | None) -> None:
        if self._stopping:
            return
        payload = desktop_state_to_payload(state, self.config) if state else None
        self.online = payload is not None
        if payload is not None:
            self.last_update = time.time()
        self.on_update(self.config.player_id, payload)

    def health(self) -> dict:
        return {
            "player_id": self.config.player_id,
            "name": self.config.name,
            "host": self.config.host,
            "port": self.config.port,
            "connected": self.connected,
            "online": self.online,
            "last_update": self.last_update,
            "last_error": self.last_error,
        }

    async def _run(self) -> None:
        base = Path(__file__).resolve().parent
        helper = (base / "player_dom.js").read_text(encoding="utf-8")
        observer = (base / "desktop_observer.js").read_text(encoding="utf-8")
        async with ClientSession(timeout=ClientTimeout(total=15)) as session:
            try:
                while not self._stopping:
                    cdp = YandexMusicCDP(session, self.config.host, self.config.port, helper, observer)
                    self._cdp = cdp
                    cdp.set_state_callback(self._set_state)
                    try:
                        while not self._stopping:
                            state = await cdp.async_state()
                            self.connected = True
                            self._set_state(state)
                            self.last_error = None
                            # Fast fallback polling on Chromium variants without push events.
                            await asyncio.sleep(self.poll_interval if cdp.observer_active else min(self.poll_interval, 4.0))
                    except asyncio.CancelledError:
                        raise
                    except (CDPError, OSError, TimeoutError) as exc:
                        self.connected = False
                        self.last_error = str(exc)
                        LOGGER.warning("Desktop %s (%s:%s) disconnected: %s",
                                       self.config.name, self.config.host, self.config.port, exc)
                        self._set_state(None)
                    except Exception as exc:
                        self.connected = False
                        self.last_error = str(exc)
                        LOGGER.exception("Unexpected Yandex Music Desktop error: %s", self.config.name)
                        self._set_state(None)
                    finally:
                        try:
                            await cdp.async_close()
                        except Exception:
                            LOGGER.exception("Error closing CDP connection for %s", self.config.name)
                        self._cdp = None
                    await asyncio.sleep(self.retry_interval)
            finally:
                self.connected = False
                self.online = False
                # Ensure stale tracks disappear immediately after shutdown.
                self.on_update(self.config.player_id, None)
