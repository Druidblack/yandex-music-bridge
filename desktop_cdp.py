from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import json
import logging
import math
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType

_LOGGER = logging.getLogger(__name__)


class CDPError(Exception):
    """Bad CDP response, disconnected player, or failed command."""


class CDPConnectionError(CDPError):
    """Unable to connect to a Yandex Music debug endpoint."""


def is_music_target(target: Mapping) -> bool:
    if not isinstance(target, Mapping) or target.get("type") != "page":
        return False
    try:
        url = urlsplit(str(target.get("url", "")))
        scheme = url.scheme.lower()
        host = (url.hostname or "").lower()
        if scheme == "music-application" and host == "desktop" and not (url.username or url.password):
            return True
        if scheme in ("https", "http") and host in {
            "music.yandex.ru", "music.yandex.com", "music.yandex.by",
            "music.yandex.kz", "music.yandex.uz",
        } and not (url.username or url.password):
            return True
        if scheme in ("file", "app"):
            title = str(target.get("title", "")).lower()
            return "yandex" in title and "music" in title or "яндекс" in title and "музыка" in title
    except ValueError:
        return False
    return False


def parse_time(value: object) -> float | None:
    """Convert either seconds or mm:ss/hh:mm:ss to seconds."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if 0 <= value < float("inf") else None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        fields = [int(part) for part in value.split(":")]
        if len(fields) > 3 or any(part < 0 for part in fields):
            return None
        total = 0
        for part in fields:
            total = total * 60 + part
        return float(total)
    except ValueError:
        return None


def normalize_state(state: Mapping) -> dict:
    """Map Ajazz's DOM snapshot to Home Assistant media-player fields."""
    duration = parse_time(state.get("totalTime"))
    position = parse_time(state.get("currentTime"))
    progress = state.get("progressValue")
    progress_max = state.get("progressMax")
    if (duration is not None and duration > 0
        and isinstance(progress, (float, int)) and not isinstance(progress, bool)
        and isinstance(progress_max, (float, int)) and not isinstance(progress_max, bool)
        and progress_max > 0):
        position = max(0.0, min(duration, progress / progress_max * duration))
    if position is not None and duration is not None:
        position = min(position, duration)
    volume = state.get("volumePercent")
    repeat = state.get("repeatMode")
    cover = state.get("coverUrl")
    if isinstance(cover, str):
        parsed = urlsplit(cover)
        if parsed.scheme != "https" or parsed.hostname not in (
            "avatars.yandex.net", "avatars.yandex.ru", "music.yandex.ru", "music.yandex.com"
        ):
            cover = None
    else:
        cover = None

    def optional_bool(value: object) -> bool | None:
        return value if isinstance(value, bool) else None

    return {
        "online": state.get("playerReady") is True,
        "title": state.get("trackTitle") or None,
        "artist": state.get("trackArtist") or None,
        "track_url": state.get("trackUrl") or None,
        "cover_url": cover,
        "playing": optional_bool(state.get("playing")),
        "muted": optional_bool(state.get("muted")),
        "liked": optional_bool(state.get("liked")),
        "shuffle": optional_bool(state.get("shuffleOn")),
        "shuffle_available": state.get("shuffleAvailable") is not False,
        "repeat": {0: "off", 1: "all", 2: "one"}.get(repeat) if isinstance(repeat, int) else None,
        "volume": max(0.0, min(1.0, float(volume) / 100.0)) if isinstance(volume, (int, float)) and not isinstance(volume, bool) else None,
        "position": position,
        "duration": duration,
    }


# Each command is executed in the already-filtered Yandex Music page context.
# Value is passed as a JSON literal rather than interpolated untrusted text.
_COMMAND_SCRIPT = r"""
(async function(action, value) {
  switch (action) {
    case 'play':
    case 'pause': {
      const current = ymDetectPlaybackIsPlaying();
      if (current === null) return { success: false, message: 'Playback state unavailable' };
      if (current === (action === 'play')) return { success: true };
      return await ymTogglePlayback();
    }
    case 'next': return await ymClickTrackControl('next');
    case 'previous': return await ymClickTrackControl('previous');
    case 'seek': return await ymSeekTo(value);
    case 'volume': return ymSetVolume(value * 100);
    case 'mute': {
      const current = ymDetectMuteIsMuted();
      if (current === null) return { success: false, message: 'Mute state unavailable' };
      return current === value ? { success: true } : await ymToggleMute();
    }
    case 'shuffle': {
      const current = ymDetectShufflePressed();
      if (!current || !current.ok) return { success: false, message: 'Shuffle unavailable' };
      return current.shuffle === value ? { success: true } : await ymToggleShuffle();
    }
    case 'repeat': {
      const current = ymDetectRepeatMode();
      if (!current || !current.ok) return { success: false, message: 'Repeat unavailable' };
      const target = { off: 0, all: 1, one: 2 }[value];
      if (target === undefined) return { success: false, message: 'Invalid repeat mode' };
      let mode = current.mode;
      for (let i = 0; i < 2 && mode !== target; i++) {
        const result = await ymToggleRepeat();
        if (!result || !result.success) return result || { success: false };
        mode = Number.isInteger(result.mode) ? result.mode : ymDetectRepeatMode().mode;
      }
      return { success: mode === target };
    }
    case 'like': {
      const current = ymDetectLikeIsLiked();
      if (current === null) return { success: false, message: 'Like state unavailable' };
      return current === value ? { success: true } : await ymClickLike();
    }
    case 'dislike': return await ymClickDislike();
    default: return { success: false, message: 'Unsupported action' };
  }
})
"""


class YandexMusicCDP:
    """A CDP WebSocket and its request/response correlation."""

    def __init__(self, session: ClientSession, host: str, port: int, helper_script: str, observer_script: str = ""):
        self.session = session
        self.host = host
        self.port = port
        self.helper_script = helper_script
        self.observer_script = observer_script
        self._on_state: Callable[[dict], None] | None = None
        self._event_version = 0
        self._last_state: dict | None = None
        self.observer_active = False
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._connect_lock = asyncio.Lock()
        self._dom_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0

    def set_state_callback(self, callback: Callable[[dict], None] | None) -> None:
        """Deliver DOM changes to HA without waiting for the next scheduled poll."""
        self._on_state = callback

    @property
    def event_version(self) -> int:
        return self._event_version

    @property
    def http_base(self) -> str:
        addr = self.host if ":" not in self.host else f"[{self.host}]"
        return f"http://{addr}:{self.port}"

    async def _discover(self) -> str:
        try:
            async with self.session.get(
                f"{self.http_base}/json/list", timeout=ClientTimeout(total=5)
            ) as response:
                if response.status != 200:
                    raise CDPConnectionError(f"CDP discovery returned HTTP {response.status}")
                targets = await response.json(content_type=None)
        except (ClientError, TimeoutError, ValueError) as exc:
            raise CDPConnectionError(f"Cannot access CDP on {self.host}:{self.port}: {exc}") from exc
        if not isinstance(targets, list):
            raise CDPConnectionError("CDP target list has invalid format")
        target = next((t for t in targets if is_music_target(t)), None)
        if target is None:
            raise CDPConnectionError("No Yandex Music page found on the given debug port")
        remote_url = str(target.get("webSocketDebuggerUrl", ""))
        try:
            parsed = urlsplit(remote_url)
        except ValueError as exc:
            raise CDPConnectionError("Invalid CDP WebSocket URL") from exc
        # /json/list advertises localhost even when reached through a NAT/port-forward.
        # Always reconnect to the configured host:port, never to the advertised host.
        if parsed.scheme not in ("ws", "wss") or not parsed.path.startswith("/devtools/page/"):
            raise CDPConnectionError("CDP did not return a page WebSocket URL")
        addr = self.host if ":" not in self.host else f"[{self.host}]"
        return urlunsplit(("ws", f"{addr}:{self.port}", parsed.path, parsed.query, ""))

    async def async_probe(self) -> None:
        """Test discovery without taking control of the player."""
        await self._discover()

    async def _connect(self) -> None:
        if self._ws is not None and not self._ws.closed:
            return
        async with self._connect_lock:
            if self._ws is not None and not self._ws.closed:
                return
            ws_url = await self._discover()
            try:
                ws = await self.session.ws_connect(
                    ws_url, heartbeat=20, max_msg_size=2 * 1024 * 1024
                )
            except (ClientError, TimeoutError) as exc:
                raise CDPConnectionError(f"Cannot open CDP WebSocket: {exc}") from exc
            self._ws = ws
            self._reader = asyncio.create_task(self._read_messages(ws), name=f"ym_cdp_{self.port}")
            self.observer_active = False
            if self.helper_script and self.observer_script:
                try:
                    await self._setup_observer()
                    self.observer_active = True
                except CDPError as exc:
                    # Older/modified Chromium builds may not support bindings.
                    # Preserve the working legacy polling path for these users.
                    _LOGGER.warning("Yandex Music events unavailable, using polling: %s", exc)

    async def _read_messages(self, ws) -> None:
        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    try:
                        body = json.loads(message.data)
                    except (ValueError, TypeError):
                        continue
                    message_id = body.get("id")
                    if message_id is not None:
                        pending = self._pending.pop(message_id, None)
                        if pending is not None and not pending.done():
                            if "error" in body:
                                pending.set_exception(CDPError(str(body["error"])))
                            else:
                                pending.set_result(body.get("result", {}))
                    elif body.get("method") == "Runtime.bindingCalled" and self._ws is ws:
                        self._handle_binding(body.get("params") or {})
                elif message.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSED):
                    break
        except (ClientError, ConnectionError) as exc:
            _LOGGER.debug("Yandex Music CDP receive error: %s", exc)
        finally:
            # A stale reader must never mark a newly reconnected socket offline.
            if self._ws is ws:
                self._ws = None
                self.observer_active = False
                self._last_state = None
                self._publish_state({"online": False})
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(CDPConnectionError("CDP WebSocket disconnected"))
                self._pending.clear()
            if not ws.closed:
                await ws.close()

    def _publish_state(self, state: dict) -> None:
        callback = self._on_state
        if callback is not None:
            try:
                callback(state)
            except Exception:
                _LOGGER.exception("Yandex Music state callback failed")

    def _handle_binding(self, params: Mapping) -> None:
        if params.get("name") != "ymBridgeCdpNotify":
            return
        try:
            raw = json.loads(params.get("payload", ""))
            if not isinstance(raw, dict):
                return
            normalized = normalize_state(raw)
            self._last_state = normalized
            self._event_version += 1
            self._publish_state(normalized)
        except (TypeError, ValueError) as exc:
            _LOGGER.debug("Invalid Yandex Music event payload: %s", exc)

    async def _setup_observer(self) -> None:
        """Install the same MutationObserver-based push updates used by Ajazz.

        AddScriptToEvaluateOnNewDocument also restores the listener on page reload.
        The dedicated binding/observer names do not replace Ajazz's own listener.
        """
        if not self.helper_script or not self.observer_script:
            return  # Discovery-only callers do not install any helpers.
        await self._call("Page.enable", {})
        await self._call("Runtime.enable", {})
        await self._call("Runtime.addBinding", {"name": "ymBridgeCdpNotify"})
        source = self.helper_script + "\n" + self.observer_script
        await self._call("Page.addScriptToEvaluateOnNewDocument", {"source": source})
        # The registration above affects future documents, so initialize the
        # page which is already running as well.
        await self._evaluate(source)

    async def _call(self, method: str, params: dict) -> dict:
        ws = self._ws
        if ws is None or ws.closed:
            raise CDPConnectionError("CDP WebSocket unavailable")
        self._next_id += 1
        message_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        try:
            await ws.send_json({"id": message_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout=8)
        except (ClientError, TimeoutError, ConnectionError) as exc:
            raise CDPConnectionError(f"CDP {method} failed: {exc}") from exc
        finally:
            self._pending.pop(message_id, None)

    async def _evaluate(self, expression: str) -> object:
        response = await self._call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        if response.get("exceptionDetails"):
            details = response["exceptionDetails"]
            raise CDPError(str(details.get("text") or details.get("exception", {}).get("description") or "JS evaluation failed"))
        return (response.get("result") or {}).get("value")

    async def _ensure_helpers(self) -> None:
        if not await self._evaluate("!!window.__YM_BRIDGE_CDP_HELPERS_READY__ && typeof ymCollectAjazzState === 'function'"):
            # Browser navigations clear the JS context: inject on first use after every navigation.
            await self._evaluate(self.helper_script)
            if not await self._evaluate("!!window.__YM_BRIDGE_CDP_HELPERS_READY__"):
                raise CDPError("Could not install Ajazz player helpers")

    async def async_state(self) -> dict:
        async with self._dom_lock:
            await self._connect()
            version_before = self._event_version
            try:
                await self._ensure_helpers()
                state = await self._evaluate("""(() => {
                    const data = ymCollectAjazzState();
                    const volume = ymGetVolume();
                    return { ...data, volumePercent: volume && volume.success ? volume.volume : null };
                })()""")
                if not isinstance(state, dict):
                    raise CDPError("Player returned no state")
                # A newer pushed update may have arrived while evaluate ran.
                if self._event_version != version_before and self._last_state is not None:
                    return self._last_state
                return normalize_state(state)
            except CDPError:
                # A page navigation may destroy the old JS context or WS session.
                # Retry on the next scheduled coordinator refresh.
                raise

    async def async_command(self, action: str, value=None) -> None:
        allowed = {
            "play", "pause", "next", "previous", "seek", "volume", "mute", "shuffle", "repeat", "like", "dislike",
        }
        if action not in allowed:
            raise CDPError("Unsupported command")
        if action in {"seek", "volume"} and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise CDPError("Expected numeric value")
        if action in {"seek", "volume"} and not math.isfinite(value):
            raise CDPError("Expected finite numeric value")
        if action in {"mute", "shuffle", "like"} and not isinstance(value, bool):
            raise CDPError("Expected true/false")
        if action == "seek" and not 0 <= value <= 86400:
            raise CDPError("Invalid seek position")
        if action == "volume" and not 0 <= value <= 1:
            raise CDPError("Invalid volume")
        if action == "repeat" and value not in {"off", "all", "one"}:
            raise CDPError("Invalid repeat mode")
        expression = f"({_COMMAND_SCRIPT})({json.dumps(action)}, {json.dumps(value)})"
        async with self._dom_lock:
            await self._connect()
            await self._ensure_helpers()
            result = await self._evaluate(expression)
            if result is True or (isinstance(result, dict) and result.get("success") is True):
                return
            reason = result.get("message") if isinstance(result, dict) else None
            raise CDPError(reason or "Player rejected command")

    async def async_close(self) -> None:
        """Close the per-instance WebSocket, not HA's shared HTTP session."""
        self._on_state = None
        async with self._connect_lock:
            ws = self._ws
            self._ws = None
            reader = self._reader
            self._reader = None
            if ws is not None:
                await ws.close()
            if reader is not None:
                reader.cancel()
                try:
                    await reader
                except asyncio.CancelledError:
                    pass
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(CDPConnectionError("CDP client closed"))
            self._pending.clear()
