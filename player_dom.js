function ymFindSonataPlayerBar() {
  return ymQueryDeep('[class*="PlayerBarDesktopWithBackgroundProgressBar_root"]')
    || ymQueryDeep('[data-test-id="PLAYERBAR_DESKTOP"]')
    || ymQueryDeep('[class*="PlayerBarDesktopWithBackgroundProgressBar_info"]');
}

function ymIsVisibleNode(node) {
  if (!node) return false;
  if (node.closest && node.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
  if (node.getClientRects && node.getClientRects().length === 0) return false;
  return true;
}

function ymFindVibeMetadataRoot() {
  var node = document.querySelector('[class*="VibePage_root"], [class*="VibePlayerbarMeta_root"], '
    + '[class*="VibePage_entityMeta"], [class*="VibePlayerbarMeta_trackNameText"]');
  return ymIsVisibleNode(node) ? node : null;
}

function ymReadMediaSessionTrack() {
  try {
    var metadata = typeof navigator !== 'undefined' && navigator.mediaSession
      ? navigator.mediaSession.metadata : null;
    if (!metadata) return null;
    var title = String(metadata.title || '').trim();
    if (!title || /^(моя волна|my vibe|яндекс музыка|yandex music)$/i.test(title)) return null;
    var artwork = metadata.artwork || [];
    var coverUrl = '';
    var bestSize = -1;
    for (var i = 0; i < artwork.length; i++) {
      var item = artwork[i];
      if (!item || !item.src) continue;
      var raw = String(item.src);
      if (raw.indexOf('//') === 0) raw = 'https:' + raw;
      else if (raw.indexOf('/') === 0 && typeof location !== 'undefined') raw = location.origin + raw;
      if (!/^https?:[/][/]/i.test(raw)) continue;
      var dims = String(item.sizes || '').match(/([0-9]+) *x *([0-9]+)/i);
      var size = dims ? Math.min(Number(dims[1]), Number(dims[2])) : 0;
      if (size >= bestSize) { bestSize = size; coverUrl = raw; }
    }
    return { title: title, artist: String(metadata.artist || '').trim(), coverUrl: coverUrl };
  } catch (err) { return null; }
}


function ymInstallMediaPositionCapture() {
  try {
    var session = typeof navigator !== 'undefined' ? navigator.mediaSession : null;
    if (!session || typeof session.setPositionState !== 'function') return;
    if (session.setPositionState.__ymAjazzPositionCapture) return;
    var original = session.setPositionState;
    var wrapped = function(positionState) {
      try {
        if (positionState && Number.isFinite(Number(positionState.duration)) && Number(positionState.duration) > 0) {
          var meta = ymReadMediaSessionTrack();
          var duration = Number(positionState.duration);
          var position = Number(positionState.position);
          window.__YM_AJAZZ_MEDIA_POSITION = {
            duration: duration,
            position: Number.isFinite(position) ? Math.max(0, Math.min(duration, position)) : 0,
            at: Date.now(),
            title: meta ? meta.title : '',
            artist: meta ? meta.artist : ''
          };
        } else {
          window.__YM_AJAZZ_MEDIA_POSITION = null;
        }
      } catch (ignored) {}
      return original.apply(this, arguments);
    };
    wrapped.__ymAjazzPositionCapture = true;
    session.setPositionState = wrapped;
  } catch (ignored) {}
}

function ymReadMediaPosition() {
  try {
    var value = window.__YM_AJAZZ_MEDIA_POSITION;
    if (!value || !Number.isFinite(value.duration) || value.duration <= 0) return null;
    var media = ymReadMediaSessionTrack();
    var title = ymIsVibePageActive() ? ymGetVibeTrackTitle() : '';
    var normal = function(value) { return String(value || '').trim().toLocaleLowerCase().replace(/ +/g, ' '); };
    if (value.title && media && normal(value.title) !== normal(media.title)) return null;
    if (value.title && !media && title && normal(value.title) !== normal(title)) return null;
    if (value.artist && media && normal(value.artist) !== normal(media.artist)) return null;
    if (Date.now() - value.at > value.duration * 1000 + 30000) return null;
    var playback = typeof navigator !== 'undefined' && navigator.mediaSession
      ? navigator.mediaSession.playbackState : '';
    var playing = playback === 'playing' ? true : playback === 'paused' ? false : ymDetectPlaybackIsPlaying();
    var elapsed = playing === true ? Math.max(0, (Date.now() - value.at) / 1000) : 0;
    var position = Math.min(value.duration, value.position + elapsed);
    return {
      success: true,
      currentTime: ymFormatSeconds(position),
      totalTime: ymFormatSeconds(value.duration),
      progressValue: position, progressMax: value.duration,
      progressPercent: 100 * position / value.duration
    };
  } catch (ignored) { return null; }
}

function ymFormatSeconds(seconds) {
  var s = Math.max(0, Math.floor(seconds));
  var minutes = Math.floor(s / 60);
  var hours = Math.floor(minutes / 60);
  return hours > 0
    ? hours + ':' + String(minutes % 60).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0')
    : minutes + ':' + String(s % 60).padStart(2, '0');
}

function ymIsMusicPlayerReady() {
  return !!(ymFindSonataPlayerBar() || ymFindVibePlayerBar()
    || (ymIsVibePageActive() && ymGetVibeTrackTitle()) || ymReadMediaSessionTrack());
}

function ymFindVibeControlsRoot() {
  return document.querySelector('[class*="VibePlayerControls_root"]');
}

function ymIsVibePageActive() {
  var vibeControls = ymFindVibeControlsRoot();
  if (vibeControls && ymIsVisibleNode(vibeControls)) {
    var rect = vibeControls.getBoundingClientRect ? vibeControls.getBoundingClientRect() : null;
    if (!rect || (rect.width > 0 && rect.height > 0)) return true;
  }
  if (ymFindVibeMetadataRoot()) return true;
  var vibePlayer = ymFindVibePlayerBar();
  if (ymIsVisibleNode(vibePlayer)) return true;
  var path = typeof location !== 'undefined' ? String(location.pathname || '') : '';
  return /(^|[/])(vibe|my-vibe)([/]|$)/i.test(path);
}

function ymFindVibePlayerBar() {
  return document.querySelector('[class*="VibePlayerBar_root"]');
}

function ymQueryDeep(selector, root) {
  root = root || document;
  if (root.querySelector) {
    var direct = root.querySelector(selector);
    if (direct) return direct;
  }
  var nodes = root.querySelectorAll ? root.querySelectorAll('*') : [];
  for (var i = 0; i < nodes.length; i++) {
    var node = nodes[i];
    if (!node.shadowRoot) continue;
    var found = ymQueryDeep(selector, node.shadowRoot);
    if (found) return found;
  }
  return null;
}

function ymCollectDeepLinks(root) {
  var links = [];
  function collect(scope) {
    if (!scope || !scope.querySelectorAll) return;
    links.push.apply(links, scope.querySelectorAll('a[href]'));
    var elements = scope.querySelectorAll('*');
    for (var i = 0; i < elements.length; i++) {
      if (elements[i].shadowRoot) collect(elements[i].shadowRoot);
    }
  }
  collect(root || document);
  return links;
}

function ymCanClick(button) {
  return !!button && !button.disabled && button.getAttribute('aria-disabled') !== 'true'
    && button.getAttribute('aria-hidden') !== 'true'
    && !(button.closest && button.closest('[hidden], [inert], [aria-hidden="true"]'))
    && !(button.getClientRects && button.getClientRects().length === 0);
}

async function ymWaitForState(read, expected) {
  for (var attempt = 0; attempt < 20; attempt++) {
    var value = read();
    if (value !== null && value !== undefined && (expected === undefined || value === expected)) return value;
    await ymWait(40);
  }
  return null;
}

function ymSetRangeValue(slider, newValue) {
  var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  nativeInputValueSetter.call(slider, newValue);
  slider.dispatchEvent(new InputEvent('input', {
    bubbles: true,
    cancelable: true,
    inputType: 'insertText',
    data: String(newValue)
  }));
  slider.dispatchEvent(new Event('change', { bubbles: true }));
}

function ymWait(ms) {
  return new Promise(function(resolve) { setTimeout(resolve, ms); });
}

function ymParseArtistLink(link) {
  if (!link) return '';
  var aria = link.getAttribute('aria-label') || '';
  if (aria.indexOf('Артист ') === 0) {
    var fromAria = aria.slice(7).trim();
    if (fromAria) return fromAria;
  }
  if (aria.indexOf('Исполнитель ') === 0) {
    var fromPerformer = aria.slice(12).trim();
    if (fromPerformer) return fromPerformer;
  }
  var title = link.getAttribute('title');
  if (title && title.trim()) return title.trim();
  var span = link.querySelector('span');
  if (span && span.textContent.trim()) return span.textContent.trim();
  var linkText = (link.textContent || '').trim();
  if (linkText) return linkText;
  return '';
}

function ymUniqueNonEmpty(items) {
  var seen = new Set();
  var out = [];
  for (var i = 0; i < items.length; i++) {
    var item = (items[i] || '').trim();
    if (!item || seen.has(item)) continue;
    seen.add(item);
    out.push(item);
  }
  return out;
}


function ymFindVibePlayButton(root) {
  root = root || ymFindVibeControlsRoot();
  if (!root) return null;
  return root.querySelector('[class*="VibePlayerControls_playButton"]')
    || root.querySelector('button[aria-label="Воспроизведение"], button[aria-label="Пауза"]');
}

function ymFindVibeSkipButton(root, direction) {
  root = root || ymFindVibeControlsRoot();
  if (!root) return null;

  if (direction === 'previous') {
    return root.querySelector('button[data-test-id="PREVIOUS_TRACK_BUTTON"]')
      || root.querySelector('button[aria-label="Предыдущая песня"]')
      || (function() {
        var icon = root.querySelector('use[href*="previous_xs"], use[*|href*="previous_xs"]');
        return icon ? icon.closest('button') : null;
      })();
  }

  if (direction === 'next') {
    return root.querySelector('button[data-test-id="NEXT_TRACK_BUTTON"]')
      || root.querySelector('button[aria-label="Следующая песня"]')
      || (function() {
        var icon = root.querySelector('use[href*="next_xs"], use[*|href*="next_xs"]');
        return icon ? icon.closest('button') : null;
      })();
  }

  return null;
}

function ymDetectVibePlaybackIsPlaying() {
  var vibePlayButton = ymFindVibePlayButton();
  if (vibePlayButton) {
    var label = vibePlayButton.getAttribute('aria-label') || '';
    if (label === 'Пауза') return true;
    if (label === 'Воспроизведение') return false;

    var useEl = vibePlayButton.querySelector('use');
    if (useEl) {
      var href = useEl.getAttribute('xlink:href') || useEl.getAttribute('href') || '';
      if (href.indexOf('pause') !== -1) return true;
      if (href.indexOf('play') !== -1) return false;
    }
  }

  if (document.querySelector('[class*="VibePage_textContainer_playing"]')) {
    return true;
  }

  return null;
}

function ymDetectSonataPlaybackIsPlaying() {
  var bar = ymFindSonataPlayerBar();
  if (!bar) return null;
  if (bar.querySelector("button[data-test-id='PAUSE_BUTTON']")) {
    return true;
  }
  if (bar.querySelector("button[data-test-id='PLAY_BUTTON']")) {
    return false;
  }
  if (bar.querySelector("svg use[*|href='/icons/sprite.svg#pause_filled_l'], svg use[href='/icons/sprite.svg#pause_filled_l']")) {
    return true;
  }
  if (bar.querySelector("svg use[*|href='/icons/sprite.svg#play_filled_l'], svg use[href='/icons/sprite.svg#play_filled_l']")) {
    return false;
  }

  return null;
}

function ymDetectPlaybackIsPlaying() {
  var detected = ymIsVibePageActive() ? ymDetectVibePlaybackIsPlaying() : ymDetectSonataPlaybackIsPlaying();
  if (detected !== null) return detected;
  try {
    var mediaSession = typeof navigator !== 'undefined' ? navigator.mediaSession : null;
    if (mediaSession && mediaSession.playbackState === 'playing') return true;
    if (mediaSession && mediaSession.playbackState === 'paused') return false;
    var audio = ymQueryDeep('audio') || ymQueryDeep('video');
    if (audio) return !audio.paused && !audio.ended;
  } catch (ignored) {}
  return null;
}

async function ymTogglePlayback() {
  try {
    var button = ymIsVibePageActive() ? ymFindVibePlayButton() : null;
    var bar = ymFindSonataPlayerBar();
    if (!ymIsVibePageActive() && !button && bar) {
      button = bar.querySelector('[data-test-id="PAUSE_BUTTON"], [data-test-id="PLAY_BUTTON"]');
      if (!button) {
        var icon = bar.querySelector('use[*|href*="pause_filled"], use[*|href*="play_filled"]');
        button = icon ? icon.closest('button') : null;
      }
    }
    if (!ymCanClick(button)) return { success: false, message: 'Воспроизведение недоступно' };
    var before = ymDetectPlaybackIsPlaying();
    button.click();
    var playing = before === null ? null : await ymWaitForState(ymDetectPlaybackIsPlaying, !before);
    return { success: true, accepted: true, confirmed: playing !== null, playing: playing, wasPlaying: before };
  } catch (error) {
    return { success: false, message: error.message };
  }
}

async function ymClickTrackControl(direction) {
  try {
    if (ymIsVibePageActive()) {
      var vibeButton = ymFindVibeSkipButton(null, direction);
      if (ymCanClick(vibeButton)) {
        vibeButton.click();
        return { success: true, accepted: true, confirmed: false, message: 'Vibe: ' + direction };
      }
      return { success: false, message: 'Управление треком недоступно' };
    }

    var playerBar = ymFindSonataPlayerBar();
    if (playerBar) {
      var testId = direction === 'previous' ? 'PREVIOUS_TRACK_BUTTON' : 'NEXT_TRACK_BUTTON';
      var sonataButton = playerBar.querySelector("[data-test-id='" + testId + "']");
      if (ymCanClick(sonataButton)) {
        sonataButton.click();
        return { success: true, accepted: true, confirmed: false, message: 'Sonata: ' + direction };
      }
    }

    var vibeFallback = ymFindVibeSkipButton(null, direction);
    if (ymCanClick(vibeFallback)) {
      vibeFallback.click();
      return { success: true, accepted: true, confirmed: false, message: 'Vibe: ' + direction };
    }

    return { success: false, message: 'Кнопка ' + direction + ' не найдена' };
  } catch (err) {
    return {
      success: false,
      message: 'Ошибка при поиске кнопки: ' + err.message,
      error: err.toString()
    };
  }
}


function ymFindVibeLikeButton() {
  var bar = ymFindVibePlayerBar();
  if (!bar) return null;
  return bar.querySelector('button[data-test-id="LIKE_BUTTON"]')
    || bar.querySelector('button[aria-label="Нравится"]')
    || (function() {
      var icon = bar.querySelector('use[href*="liked_xs"], use[*|href*="liked_xs"], use[href*="like_xs"], use[*|href*="like_xs"]');
      return icon ? icon.closest('button') : null;
    })();
}

function ymFindVibeDislikeButton() {
  var bar = ymFindVibePlayerBar();
  if (!bar) return null;
  return bar.querySelector('button[data-test-id="DISLIKE_BUTTON"]')
    || bar.querySelector('button[aria-label="Не нравится"]')
    || (function() {
      var icon = bar.querySelector('use[href*="dislike_xs"], use[*|href*="dislike_xs"]');
      return icon ? icon.closest('button') : null;
    })();
}

function ymFindSonataLikeButton() {
  var playerBar = ymFindSonataPlayerBar();
  if (!playerBar) return null;
  var likeButton = playerBar.querySelector("[data-test-id='LIKE_BUTTON']");
  if (likeButton) return likeButton;
  return null;
}

function ymFindSonataDislikeButton() {
  var playerBar = ymFindSonataPlayerBar();
  if (!playerBar) return null;
  var dislikeButton = playerBar.querySelector("[data-test-id='DISLIKE_BUTTON']");
  if (dislikeButton) return dislikeButton;
  return null;
}

function ymIsButtonLiked(button) {
  if (!button) return false;
  var isLiked = button.getAttribute('aria-pressed') === 'true';
  var useEl = button.querySelector('use');
  if (useEl) {
    var href = useEl.getAttribute('xlink:href') || useEl.getAttribute('href') || '';
    if (href.indexOf('liked') !== -1) isLiked = true;
  }
  return isLiked;
}

function ymDetectLikeIsLiked() {
  if (ymIsVibePageActive()) {
    var vibeLike = ymFindVibeLikeButton();
    if (vibeLike) return ymIsButtonLiked(vibeLike);
  }
  var sonataLike = ymFindSonataLikeButton();
  if (sonataLike) return ymIsButtonLiked(sonataLike);
  return null;
}

async function ymClickLike() {
  try {
    var button = ymIsVibePageActive() ? ymFindVibeLikeButton() : ymFindSonataLikeButton();
    if (!ymCanClick(button)) return { success: false, message: 'Лайк недоступен' };
    var before = ymIsButtonLiked(button);
    button.click();
    var liked = await ymWaitForState(ymDetectLikeIsLiked, !before);
    return { success: true, accepted: true, confirmed: liked !== null, liked: liked };
  } catch (error) {
    return { success: false, message: error.message };
  }
}

function ymClickDislike() {
  var button = ymIsVibePageActive() ? ymFindVibeDislikeButton() : ymFindSonataDislikeButton();
  if (!ymCanClick(button)) return { success: false, message: 'Дизлайк недоступен' };
  button.click();
  return { success: true, accepted: true, confirmed: false };
}


function ymFindVolumeRoot() {
  if (ymIsVibePageActive()) {
    var vibeBar = ymFindVibePlayerBar();
    if (vibeBar) {
      var vibeRoot = vibeBar.querySelector('[class*="ChangeVolume_root"]');
      if (vibeRoot) return vibeRoot;
    }
  }
  var sliderByTestId = document.querySelector('[data-test-id="VOLUME_SLIDER"]');
  if (sliderByTestId) {
    var rootFromSlider = sliderByTestId.closest('[class*="ChangeVolume_root"]');
    if (rootFromSlider) return rootFromSlider;
  }
  return document.querySelector('[class*="ChangeVolume_root"]');
}

function ymFindVolumeSlider() {
  var root = ymFindVolumeRoot();
  if (root) {
    var slider = root.querySelector('input[type="range"][aria-label="Управление громкостью"]')
      || root.querySelector('input[class*="ChangeVolume_slider"]')
      || root.querySelector('input[type="range"]');
    if (slider) return slider;
  }
  return document.querySelector('[data-test-id="VOLUME_SLIDER"]')
    || document.querySelector('input[class*="ChangeVolume_slider"]');
}

function ymFindMuteButton() {
  var root = ymFindVolumeRoot();
  if (root) {
    var btn = root.querySelector('[class*="ChangeVolume_button"]')
      || root.querySelector('button[aria-label="Выключить звук"], button[aria-label="Включить звук"]');
    if (btn) return btn;
  }
  return document.querySelector("button[class*='ChangeVolume_button'][data-test-id='CHANGE_VOLUME_BUTTON']")
    || document.querySelector('button[aria-label="Выключить звук"], button[aria-label="Включить звук"]');
}

function ymDetectMuteIsMuted() {
  var muteButton = ymFindMuteButton();
  if (muteButton) {
    if (muteButton.getAttribute('aria-label') === 'Включить звук') return true;
    var useEl = muteButton.querySelector('use');
    if (useEl) {
      var href = useEl.getAttribute('xlink:href') || useEl.getAttribute('href') || '';
      if (href.indexOf('volumeOff') !== -1) return true;
    }
    return false;
  }
  return null;
}

async function ymToggleMute() {
  var button = ymFindMuteButton();
  if (!ymCanClick(button)) return { success: false, message: 'Управление звуком недоступно' };
  var before = ymDetectMuteIsMuted();
  button.click();
  var muted = before === null ? null : await ymWaitForState(ymDetectMuteIsMuted, !before);
  return { success: true, accepted: true, confirmed: muted !== null, muted: muted, wasMuted: before };
}

function ymGetVolume() {
  var volumeSlider = ymFindVolumeSlider();
  if (!volumeSlider || volumeSlider.disabled) return { success: false, message: 'Слайдер громкости не найден' };
  var value = parseFloat(volumeSlider.value) || 0;
  var max = parseFloat(volumeSlider.max) || 1;
  return {
    success: true,
    volume: (value / max) * 100,
    rawValue: value,
    max: max
  };
}

function ymSetVolume(percent) {
  var volumeSlider = ymFindVolumeSlider();
  if (!volumeSlider || volumeSlider.disabled) return { success: false, message: 'Слайдер громкости не найден' };
  var clampedPercent = Math.max(0, Math.min(100, percent));
  var max = parseFloat(volumeSlider.max) || 1;
  var newValue = (clampedPercent / 100) * max;
  ymSetRangeValue(volumeSlider, newValue);
  return {
    success: true,
    volume: clampedPercent,
    rawValue: newValue,
    actualValue: parseFloat(volumeSlider.value)
  };
}

function ymChangeVolume(delta) {
  var current = ymGetVolume();
  if (!current.success) return current;
  var result = ymSetVolume(current.volume + delta);
  if (result.success) {
    result.muted = ymDetectMuteIsMuted();
  }
  return result;
}


function ymGetVibeTrackTitle() {
  var selector = '[class*="VibePlayerbarTrackName_trackNameText"]:not([aria-hidden="true"]), [class*="VibePlayerbarMeta_trackNameText"], [class*="VibePage_trackNameText"], '
    + '[class*="VibePage_trackTitle"], [class*="VibePlayerbarMeta_title"]';
  var nodes = document.querySelectorAll(selector);
  for (var i = 0; i < nodes.length; i++) {
    if (!ymIsVisibleNode(nodes[i])) continue;
    var text = (nodes[i].textContent || '').trim();
    if (text) return text;
  }
  return '';
}

function ymGetVibeTrackArtist() {
  var names = [];
  var meta = document.querySelector('[class*="VibePage_entityMeta"]');

  if (meta) {
    var separatedBlocks = meta.querySelectorAll('[class*="SeparatedArtists"]');
    for (var s = 0; s < separatedBlocks.length; s++) {
      var block = separatedBlocks[s];
      var blockTitle = block.getAttribute('title');
      if (blockTitle && blockTitle.trim()) names.push(blockTitle.trim());
      var blockLinks = block.querySelectorAll('a[href*="artist"]');
      for (var bl = 0; bl < blockLinks.length; bl++) {
        var blockName = ymParseArtistLink(blockLinks[bl]);
        if (blockName) names.push(blockName);
      }
    }

    if (!names.length) {
      var metaLinks = meta.querySelectorAll('a[href*="artist"]');
      for (var ml = 0; ml < metaLinks.length; ml++) {
        var metaName = ymParseArtistLink(metaLinks[ml]);
        if (metaName) names.push(metaName);
      }
    }
  }

  if (!names.length) {
    var dynamicRoots = document.querySelectorAll('[class*="VibeDynamicArtists"]');
    for (var d = 0; d < dynamicRoots.length; d++) {
      var dynamicLinks = dynamicRoots[d].querySelectorAll('a[href*="artist"]');
      for (var dl = 0; dl < dynamicLinks.length; dl++) {
        var dynamicName = ymParseArtistLink(dynamicLinks[dl]);
        if (dynamicName) names.push(dynamicName);
      }
    }
  }

  if (!names.length) {
    var fallbackLink = document.querySelector('[class*="VibeDynamicArtists"] a[href*="artist"]')
      || document.querySelector('[class*="SeparatedArtists"] a[href*="artist"]')
      || document.querySelector('[class*="VibePage_entityMeta"] a[href*="artist"]');
    var fallbackName = ymParseArtistLink(fallbackLink);
    if (fallbackName) names.push(fallbackName);
  }

  if (!names.length) {
    var fallback = document.querySelector('[class*="VibePlayerbarMeta_artist"], [class*="VibePage_artistName"]');
    if (ymIsVisibleNode(fallback)) names.push((fallback.textContent || '').trim());
  }
  return ymUniqueNonEmpty(names).join(', ');
}


function ymFindSeekSlider() {
  if (ymIsVibePageActive()) {
    var vibeSlider = ymQueryDeep('input[aria-label="Управление таймкодом"]')
      || ymQueryDeep('[class*="VibePlayerbarMeta_slider"]');
    if (vibeSlider) return vibeSlider;
  }
  return document.querySelector('[data-test-id="TIMECODE_SLIDER"]');
}

function ymParseTimecodeOverlay(text) {
  if (!text) return null;
  var parts = text.split('/').map(function(s) { return s.trim(); });
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
  return { currentTime: parts[0], totalTime: parts[1] };
}

function ymReadAudioTime() {
  try {
    var audioElements = document.querySelectorAll('audio');
    var audio = null;
    for (var i = 0; i < audioElements.length; i++) {
      var item = audioElements[i];
      if (!item.paused && !item.ended && Number.isFinite(item.duration) && item.duration > 0) {
        audio = item;
        break;
      }
      if (!audio && Number.isFinite(item.duration) && item.duration > 0) audio = item;
    }
    if (!audio) audio = ymQueryDeep('audio') || ymQueryDeep('video');
    if (!audio || !Number.isFinite(audio.duration) || audio.duration <= 0) return null;
    var duration = audio.duration;
    var position = Number.isFinite(audio.currentTime) ? Math.max(0, Math.min(duration, audio.currentTime)) : 0;
    function format(seconds) {
      var total = Math.floor(seconds);
      return Math.floor(total / 60) + ':' + String(total % 60).padStart(2, '0');
    }
    return {
      success: true,
      currentTime: format(position), totalTime: format(duration),
      progressValue: position, progressMax: duration,
      progressPercent: position / duration * 100
    };
  } catch (err) { return null; }
}


function ymReadVisibleTrackTime() {
  try {
    var current = ymQueryDeep('[data-test-id="TIMECODE_TIME_START"]')
      || ymQueryDeep('[class*="Timecode_timeStart"]')
      || ymQueryDeep('[class*="Timecode_currentTime"]');
    var total = ymQueryDeep('[data-test-id="TIMECODE_TIME_END"]')
      || ymQueryDeep('[class*="Timecode_timeEnd"]')
      || ymQueryDeep('[class*="Timecode_totalTime"]');
    var slider = ymFindSeekSlider();
    var rawCurrent = current ? String(current.textContent || '').trim() : '';
    var rawTotal = total ? String(total.textContent || '').trim() : '';
    if (!rawCurrent || !rawTotal) return null;
    var parse = function(str) {
      if (!/^\d{1,3}:\d{2}(?::\d{2})?$/.test(str)) return null;
      return str.split(':').reduce(function(acc, part) { return 60 * acc + Number(part); }, 0);
    };
    var time = parse(rawCurrent);
    var duration = parse(rawTotal);
    if (time === null || duration === null || duration <= 0) return null;
    var value = slider ? Number(slider.value) : NaN;
    var max = slider ? Number(slider.max) : NaN;
    if (!Number.isFinite(value) || !Number.isFinite(max) || max <= 0) {
      value = time;
      max = duration;
    }
    return {
      success: true, currentTime: rawCurrent, totalTime: rawTotal,
      progressValue: value, progressMax: max,
      progressPercent: value / max * 100
    };
  } catch (ignored) { return null; }
}

function ymGetTrackTime() {
  try {
    if (ymIsVibePageActive()) {
      var timeOverlay = ymQueryDeep('[class*="VibePlayerbarMeta_timecodeOverlay"]');
      var slider = ymFindSeekSlider();
      if (timeOverlay) {
        var parsed = ymParseTimecodeOverlay(timeOverlay.textContent);
        if (parsed) {
          var progressValue = slider ? Number(slider.value) : null;
          var progressMax = slider ? Number(slider.max) : null;
          return {
            success: true,
            currentTime: parsed.currentTime,
            totalTime: parsed.totalTime,
            progressValue: progressValue,
            progressMax: progressMax,
            progressPercent: progressMax ? (progressValue / progressMax) * 100 : 0
          };
        }
      }
    }

    var visibleTime = ymReadVisibleTrackTime();
    if (visibleTime) return visibleTime;
    var audioTime = ymReadAudioTime();
    if (audioTime) return audioTime;
    var mediaTime = ymReadMediaPosition();
    if (mediaTime) return mediaTime;

    var playerBar = ymFindSonataPlayerBar();
    if (!playerBar) return {
      success: false,
      message: 'Данные о времени пока недоступны',
      transient: ymIsVibePageActive()
    };

    var currentTimeElement = playerBar.querySelector('[data-test-id="TIMECODE_TIME_START"]');
    var totalTimeElement = playerBar.querySelector('[data-test-id="TIMECODE_TIME_END"]');
    var progressSlider = playerBar.querySelector('[data-test-id="TIMECODE_SLIDER"]') || ymFindSeekSlider();

    if (currentTimeElement && totalTimeElement && progressSlider) {
      var progressValueSonata = parseFloat(progressSlider.value) || 0;
      var progressMaxSonata = parseFloat(progressSlider.max) || 100;
      return {
        success: true,
        currentTime: currentTimeElement.textContent.trim(),
        totalTime: totalTimeElement.textContent.trim(),
        progressValue: progressValueSonata,
        progressMax: progressMaxSonata,
        progressPercent: (progressValueSonata / progressMaxSonata) * 100
      };
    }

    return ymReadAudioTime() || { success: false, message: 'Не удалось найти элементы времени трека' };
  } catch (err) {
    return { success: false, message: err.message, error: err.toString() };
  }
}

function ymAlbumUrlFromCoverUrl(coverUrl) {
  if (!coverUrl) return '';
  var match = String(coverUrl).match(/\.a\.(\d+)(?:-\d+)?(?:\/|$)/);
  if (!match) return '';
  var origin = (typeof window !== 'undefined' && window.location && window.location.origin && window.location.origin.indexOf('yandex.') !== -1)
    ? window.location.origin
    : 'https://music.yandex.ru';
  return origin + '/album/' + match[1];
}

function ymHrefHasAlbum(href) {
  return String(href || '').toLowerCase().indexOf('album') !== -1;
}

function ymNormalizeMusicUrl(href, requireTrack) {
  if (!href) return '';
  var raw = String(href).trim();
  if (!raw) return '';
  if (raw.indexOf('//') === 0) raw = 'https:' + raw;
  if (raw.indexOf('/') === 0) {
    var origin = (typeof window !== 'undefined' && window.location && window.location.origin && window.location.origin.indexOf('yandex.') !== -1)
      ? window.location.origin
      : 'https://music.yandex.ru';
    raw = origin + raw;
  }
  if (raw.indexOf('http://') !== 0 && raw.indexOf('https://') !== 0) return '';
  if (raw.indexOf('yandex.') === -1) return '';
  try {
    var parsed = new URL(raw);
    if (!/^music\.yandex\.(ru|com|by|kz|uz)$/.test(parsed.hostname)) return '';
    var base = parsed.origin;
    var albumId = parsed.searchParams.get('albumId');
    var trackId = parsed.searchParams.get('trackId');
    if (albumId && trackId) {
      return base + '/album/' + albumId + '/track/' + trackId;
    }
    if (albumId && !requireTrack) {
      return base + '/album/' + albumId;
    }
    var path = (parsed.pathname || '').replace(/\/+$/, '');
    var trackMatch = path.match(/\/album\/(\d+)\/track\/(\d+)$/i);
    if (trackMatch) {
      return base + '/album/' + trackMatch[1] + '/track/' + trackMatch[2];
    }
    var albumMatch = path.match(/\/album\/(\d+)$/i);
    if (albumMatch) {
      if (requireTrack) return '';
      return base + '/album/' + albumMatch[1];
    }
  } catch (err) {
    return '';
  }
  return '';
}

function ymExtractMusicUrlFromNode(node, requireTrack) {
  if (!node) return '';
  if (node.matches && node.matches('a') && ymHrefHasAlbum(node.getAttribute('href') || node.href || '')) {
    return ymNormalizeMusicUrl(node.getAttribute('href') || node.href || '', requireTrack);
  }
  if (node.closest) {
    var link = node.closest('a[class*="AlbumCover_link"], a[class*="Meta_albumLink"], a[href*="album"]');
    if (link) return ymNormalizeMusicUrl(link.getAttribute('href') || link.href || '', requireTrack);
  }
  return '';
}

function ymExtractCoverUrlFromNode(node) {
  if (!node) return '';
  var tag = String(node.tagName || '').toUpperCase();
  if (tag === 'IMG') {
    return node.currentSrc || node.src || '';
  }
  if (node.querySelector) {
    var img = node.querySelector('img[src], img[srcset], source[srcset]');
    if (img) {
      if (img.currentSrc) return img.currentSrc;
      if (img.src) return img.src;
      var srcset = img.getAttribute('srcset') || '';
      if (srcset) return srcset.split(',')[0].trim().split(/\s+/)[0];
    }
  }
  var style = node.style && node.style.backgroundImage;
  if ((!style || style === 'none') && typeof window !== 'undefined' && window.getComputedStyle) {
    try {
      style = window.getComputedStyle(node).backgroundImage;
    } catch (err) {}
  }
  if (style && style !== 'none') {
    var match = String(style).match(/url\(["']?(.*?)["']?\)/);
    if (match && match[1]) return match[1];
  }
  return '';
}

function ymFindVibeCoverImage() {
  var candidates = [
    ymQueryDeep('[class*="VibePlayerBar_root"] a[class*="AlbumCover_link"]'),
    ymQueryDeep('[class*="VibePlayerBar_root"] [class*="AlbumCover_cover"]'),
    ymQueryDeep('[class*="VibePlayerBar_root"] [class*="AlbumCover_root"]'),
    ymQueryDeep('[class*="VibePlayerBar_root"] img'),
    ymQueryDeep('[class*="VibePage_root"] [class*="AlbumCover_cover"]'),
    ymQueryDeep('[class*="VibePage_root"] img[class*="AlbumCover"]'),
    ymQueryDeep('[class*="VibePlayerbarMeta_root"] img')
  ];
  for (var i = 0; i < candidates.length; i++) {
    if (candidates[i]) return candidates[i];
  }
  return null;
}

function ymResolveVibeCoverUrl() {
  var candidates = [
    ymQueryDeep('[class*="VibePlayerBar_root"] a[class*="AlbumCover_link"]'),
    ymQueryDeep('[class*="VibePlayerBar_root"] [class*="AlbumCover_cover"]'),
    ymQueryDeep('[class*="VibePlayerBar_root"] [class*="AlbumCover_root"]'),
    ymQueryDeep('[class*="VibePlayerBar_root"] img'),
    ymQueryDeep('[class*="VibePage_root"] [class*="AlbumCover_cover"]'),
    ymQueryDeep('[class*="VibePage_root"] img[class*="AlbumCover"]'),
    ymQueryDeep('[class*="VibePlayerbarMeta_root"] img')
  ];
  for (var i = 0; i < candidates.length; i++) {
    var url = ymExtractCoverUrlFromNode(candidates[i]);
    if (url) return url;
  }
  var media = ymReadMediaSessionTrack();
  return media ? media.coverUrl : '';
}

function ymFindVibeAlbumUrl(coverUrl) {
  var vibeRoot = ymQueryDeep('[class*="VibePlayerBar_root"]');
  var scopes = [];
  if (vibeRoot) scopes.push(vibeRoot);

  var selectors = [
    'a[class*="AlbumCover_link"]',
    'a[href*="albumId="]',
    'a[href*="album"]'
  ];
  for (var r = 0; r < scopes.length; r++) {
    for (var s = 0; s < selectors.length; s++) {
      var link = ymQueryDeep(selectors[s], scopes[r]);
      if (!link) continue;
      var url = ymNormalizeMusicUrl(link.getAttribute('href') || link.href || '', false);
      if (url) return url;
    }
  }
  var coverNode = ymFindVibeCoverImage();
  var fromCoverNode = ymExtractMusicUrlFromNode(coverNode, false);
  if (fromCoverNode) return fromCoverNode;
  return ymAlbumUrlFromCoverUrl(coverUrl) || ymFindMusicUrlDeep(false);
}

function ymFindMusicUrlDeep(requireTrack) {
  var scope = ymIsVibePageActive() ? ymFindVibePlayerBar() : ymFindSonataPlayerBar();
  if (!scope) return '';
  var links = ymCollectDeepLinks(scope);
  var best = '';
  var bestScore = -1;
  for (var i = 0; i < links.length; i++) {
    var link = links[i];
    var href = link.getAttribute('href') || link.href || '';
    if (!ymHrefHasAlbum(href)) continue;
    var url = ymNormalizeMusicUrl(href, requireTrack);
    if (!url) continue;
    var className = String(link.className || '');
    var aria = link.getAttribute('aria-label') || '';
    var score = 0;
    if (className.indexOf('Meta_albumLink') !== -1) score += 100;
    if (className.indexOf('AlbumCover_link') !== -1) score += 90;
    if (aria.indexOf('Трек') === 0) score += 80;
    if (link.closest && link.closest('[class*="PlayerBarDesktopWithBackgroundProgressBar"]')) score += 70;
    if (link.closest && link.closest('[class*="VibePlayerBar_root"]')) score += 60;
    if (link.closest && link.closest('[class*="AlbumCover_root"]')) score += 50;
    if (href.indexOf('/track/') !== -1 || href.indexOf('trackId=') !== -1) score += 40;
    if (score > bestScore) {
      bestScore = score;
      best = url;
    }
  }
  return best;
}


function ymNormalizeTrackName(text) {
  return String(text || '').trim().toLocaleLowerCase().replace(/\s+/g, ' ');
}

function ymGetVibeTrackUrlFromNodes() {
  var meta = ymQueryDeep('[class*="VibePage_entityMeta"], [class*="VibePlayerbarMeta_root"]')
    || ymFindVibeMetadataRoot();
  if (!meta) return '';
  var title = ymNormalizeTrackName(ymGetVibeTrackTitle());
  if (!title) return '';
  var links = ymCollectDeepLinks(meta);
  var matching = [];
  for (var i = 0; i < links.length; i++) {
    var url = ymNormalizeMusicUrl(links[i].getAttribute('href') || links[i].href || '', true);
    if (!url) continue;
    var label = ymNormalizeTrackName(links[i].getAttribute('aria-label') || links[i].textContent);
    if (label === title || label.indexOf(title + ' ') === 0) return url;
    matching.push(url);
  }
  var className = String(meta.className || '');
  if (matching.length === 1 && (/VibePage_entityMeta|VibePlayerbarMeta_root/.test(className))) {
    return matching[0];
  }
  return '';
}

function ymFindVibeReactTrackUrl(title) {
  var player = ymFindVibePlayerBar();
  if (!player || !title || ymNormalizeTrackName(ymGetVibeTrackTitle()) !== ymNormalizeTrackName(title)) return '';
  var albumNode = player.querySelector('a[class*="AlbumCover_link"]');
  var albumUrl = albumNode ? ymNormalizeMusicUrl(albumNode.getAttribute('href'), false) : '';
  var album = albumUrl.match(/\/album\/(\d+)$/);
  if (!album) return '';
  var nodes = [player].concat(Array.from(player.querySelectorAll('*')));
  var queue = [];
  for (var i = 0; i < nodes.length; i++) {
    var key = Object.keys(nodes[i]).find(function(key) { return key.indexOf('__reactProps$') === 0; });
    if (key) queue.push({ object: nodes[i][key], depth: 0 });
  }
  var seen = new Set();
  var ids = new Set();
  var budget = 0;
  while (queue.length && budget++ < 1500) {
    var next = queue.shift();
    var obj = next.object;
    if (!obj || typeof obj !== 'object' || seen.has(obj)) continue;
    seen.add(obj);
    if (/^\d+$/.test(String(obj.trackId || ''))) ids.add(String(obj.trackId));
    if (ids.size > 1) return '';
    if (next.depth >= 6) continue;
    var keys = Object.keys(obj);
    for (var j = 0; j < keys.length; j++) {
      var name = keys[j];
      if (name === '_owner' || name === 'ref') continue;
      var value = obj[name];
      if (value && typeof value === 'object') queue.push({ object: value, depth: next.depth + 1 });
    }
  }
  if (queue.length || ids.size !== 1) return '';
  return ymNormalizeMusicUrl('/album/' + album[1] + '/track/' + Array.from(ids)[0], true);
}

async function ymLookupCurrentTrackUrl() {
  var before = ymGetTrackInfo();
  if (!before.success || !before.title) return { success: false, reason: 'no-track' };
  var link = ymNormalizeMusicUrl(before.trackUrl, true);
  if (!link && ymIsVibePageActive()) {
    link = ymGetVibeTrackUrlFromNodes() || ymFindVibeReactTrackUrl(before.title);
  }
  if (!link && ymIsVibePageActive()) {
    try {
      var query = (before.artist ? before.artist + ' ' : '') + before.title;
      var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      var stop = controller ? setTimeout(function() { controller.abort(); }, 2200) : null;
      var response;
      try {
        response = await fetch('https://api.music.yandex.net/search?type=track&page=0&text='
          + encodeURIComponent(query), controller ? { signal: controller.signal } : {});
      } finally { if (stop) clearTimeout(stop); }
      if (response && response.ok) {
        var result = await response.json();
        var catalog = result.result || result;
        var section = catalog && catalog.tracks;
        var tracks = section && (section.results || section.items) || [];
        var matches = [];
        for (var n = 0; n < tracks.length && n < 20; n++) {
          var item = tracks[n];
          if (!item || ymNormalizeTrackName(item.title) !== ymNormalizeTrackName(before.title)) continue;
          var art = Array.isArray(item.artists) ? item.artists.map(function(a) { return a.name || ''; }) : [];
          var wanted = ymNormalizeTrackName(before.artist);
          if (wanted && !art.some(function(a) { return wanted.indexOf(ymNormalizeTrackName(a)) !== -1
              || ymNormalizeTrackName(a).indexOf(wanted) !== -1; })) continue;
          var albums = Array.isArray(item.albums) ? item.albums : [];
          if (!albums.length || !/^\d+$/.test(String(item.id || '')) || !/^\d+$/.test(String(albums[0].id || ''))) continue;
          var candidate = ymNormalizeMusicUrl('/album/' + albums[0].id + '/track/' + item.id, true);
          if (candidate && matches.indexOf(candidate) < 0) matches.push(candidate);
        }
        if (matches.length === 1) link = matches[0];
        if (matches.length > 1) {
          var expectedAlbum = ymAlbumUrlFromCoverUrl(before.coverUrl);
          var albumId = String(expectedAlbum).match(/\/album\/(\d+)$/);
          if (albumId) link = matches.find(function(url) { return url.indexOf('/album/' + albumId[1] + '/track/') !== -1; }) || '';
        }
      }
    } catch (ignored) {}
  }
  var after = ymGetTrackInfo();
  if (!after.success || ymNormalizeTrackName(after.title) !== ymNormalizeTrackName(before.title)
      || ymNormalizeTrackName(after.artist) !== ymNormalizeTrackName(before.artist)) {
    return { success: false, reason: 'track-changed' };
  }
  return { success: !!link, trackUrl: link || '', title: before.title, artist: before.artist };
}

function ymFindSonataTrackUrl(playerBar) {
  var directLink = playerBar ? playerBar.querySelector('a[class*="Meta_albumLink"]') : null;
  if (directLink) {
    var directUrl = ymNormalizeMusicUrl(directLink.getAttribute('href') || directLink.href || '', true);
    if (directUrl) return directUrl;
  }
  return ymFindMusicUrlDeep(true);
}

function ymResolveTrackPageUrl(coverUrl) {
  var sonataUrl = ymFindSonataTrackUrl(ymFindSonataPlayerBar());
  if (sonataUrl) return sonataUrl;
  var resolvedCover = coverUrl || ymResolveVibeCoverUrl();
  return ymFindVibeAlbumUrl(resolvedCover);
}

function ymDebugTrackUrlLookup(coverUrl) {
  var playerBar = ymFindSonataPlayerBar();
  var albumLink = playerBar ? playerBar.querySelector('a[class*="Meta_albumLink"]') : null;
  var vibeLink = ymQueryDeep('a[class*="AlbumCover_link"]') || ymQueryDeep('a[href*="albumId="]');
  var resolvedCover = coverUrl || ymResolveVibeCoverUrl();
  var links = ymCollectDeepLinks(document);
  var samples = [];
  for (var i = 0; i < links.length; i++) {
    var href = links[i].getAttribute('href') || links[i].href || '';
    if (!ymHrefHasAlbum(href)) continue;
    samples.push({
      href: href,
      className: String(links[i].className || '').slice(0, 100),
      aria: links[i].getAttribute('aria-label') || '',
      track: ymNormalizeMusicUrl(href, true),
      album: ymNormalizeMusicUrl(href, false)
    });
    if (samples.length >= 10) break;
  }
  return {
    vibeActive: ymIsVibePageActive(),
    playerBarFound: !!playerBar,
    albumLinkHref: albumLink ? (albumLink.getAttribute('href') || albumLink.href || '') : '',
    vibeLinkHref: vibeLink ? (vibeLink.getAttribute('href') || vibeLink.href || '') : '',
    coverUrl: resolvedCover,
    coverAlbumUrl: ymAlbumUrlFromCoverUrl(resolvedCover),
    albumLinkCount: samples.length,
    albumLinkSamples: samples,
    resolvedTrack: ymFindMusicUrlDeep(true),
    resolvedAlbum: ymFindMusicUrlDeep(false),
    resolved: ymResolveTrackPageUrl(resolvedCover)
  };
}

function ymUpscaleCoverUrl(url) {
  if (!url) return url;
  if (url.includes('/100x100')) return url.replace('/100x100', '/400x400');
  if (url.includes('/200x200')) return url.replace('/200x200', '/400x400');
  return url;
}

function ymGetTrackInfo() {
  try {
    if (ymIsVibePageActive()) {
      var title = ymGetVibeTrackTitle();
      if (title) {
        var mediaVibe = ymReadMediaSessionTrack();
        if (mediaVibe && mediaVibe.title !== title) mediaVibe = null;
        var artist = ymGetVibeTrackArtist() || (mediaVibe ? mediaVibe.artist : '');
        var originalCoverUrl = ymResolveVibeCoverUrl() || (mediaVibe ? mediaVibe.coverUrl : '');
        var trackUrl = ymGetVibeTrackUrlFromNodes() || ymFindVibeReactTrackUrl(title);
        return {
          success: true,
          title: title,
          artist: artist,
          coverUrl: ymUpscaleCoverUrl(originalCoverUrl),
          originalCoverUrl: originalCoverUrl,
          trackUrl: trackUrl
        };
      }
    }


    var playerBar = ymFindSonataPlayerBar();
    var coverImgSonata = playerBar
      ? (playerBar.querySelector('img[class*="PlayerBarDesktopWithBackgroundProgressBar_cover"]')
        || playerBar.querySelector('[data-test-id="PLAYERBAR_DESKTOP"] img'))
      : null;
    var titleElement = playerBar
      ? (playerBar.querySelector('a[class*="Meta_albumLink"] [class*="Meta_title"]')
        || playerBar.querySelector('a[class*="Meta_albumLink"]')
        || playerBar.querySelector('[class*="Meta_title"]')
        || playerBar.querySelector('[data-test-id="TRACK_TITLE"] [class*="Meta_title"]')
        || playerBar.querySelector('[data-test-id="TRACK_TITLE"] a')
        || playerBar.querySelector('[data-test-id="TRACK_TITLE"]'))
      : null;
    var artistElement = playerBar
      ? (playerBar.querySelector('[class*="Meta_artists"] a')
        || playerBar.querySelector('[data-test-id="SEPARATED_ARTIST_TITLE"] [class*="Meta_artist"]')
        || playerBar.querySelector('[data-test-id="SEPARATED_ARTIST_TITLE"] a')
        || playerBar.querySelector('[data-test-id="SEPARATED_ARTIST_TITLE"]'))
      : null;
    var titleSonata = titleElement ? (titleElement.textContent || '').trim() : '';
    var artistLinks = playerBar ? playerBar.querySelectorAll('[class*="Meta_artists"] a, [data-test-id="SEPARATED_ARTIST_TITLE"] a') : [];
    var artistNames = [];
    for (var ai = 0; ai < artistLinks.length; ai++) artistNames.push(ymParseArtistLink(artistLinks[ai]));
    var artistSonata = ymUniqueNonEmpty(artistNames).join(', ') || (artistElement ? (artistElement.textContent || '').trim() : '');

    if (titleSonata) {
      var originalCoverUrlSonata = coverImgSonata ? (coverImgSonata.currentSrc || coverImgSonata.src || '') : '';
      var trackUrlSonata = ymExtractMusicUrlFromNode(titleElement, true)
        || ymExtractMusicUrlFromNode(playerBar.querySelector('a[class*="Meta_albumLink"]'), true)
        || ymResolveTrackPageUrl(originalCoverUrlSonata);
      return {
        success: true,
        title: titleSonata,
        artist: artistSonata,
        coverUrl: ymUpscaleCoverUrl(originalCoverUrlSonata),
        originalCoverUrl: originalCoverUrlSonata,
        trackUrl: trackUrlSonata
      };
    }

    var media = ymReadMediaSessionTrack();
    if (media) {
      return {
        success: true,
        title: media.title, artist: media.artist,
        coverUrl: ymUpscaleCoverUrl(media.coverUrl),
        originalCoverUrl: media.coverUrl,
        trackUrl: ''
      };
    }
    if (!playerBar) return {
      success: false, message: 'Метаданные трека пока недоступны',
      transient: ymIsVibePageActive()
    };
    return { success: false, message: 'Не удалось найти информацию о треке' };
  } catch (err) {
    return { success: false, message: err.message, error: err.toString() };
  }
}

function ymSeekTo(position) {
  try {
    var progressSlider = ymFindSeekSlider();
    if (!progressSlider) return { success: false, message: 'Прогресс-бар не найден' };
    var maxValue = parseFloat(progressSlider.max) || 100;
    var newValue = Math.max(0, Math.min(maxValue, position));
    ymSetRangeValue(progressSlider, newValue);
    return { success: true, position: newValue, max: maxValue };
  } catch (err) {
    return { success: false, message: err.message, error: err.toString() };
  }
}

function ymSeekRelative(deltaSeconds) {
  try {
    var progressSlider = ymFindSeekSlider();
    if (!progressSlider) return { success: false, message: 'Прогресс-бар не найден' };
    var currentValue = parseFloat(progressSlider.value) || 0;
    var maxValue = parseFloat(progressSlider.max) || 100;
    if (!ymCanClick(progressSlider)) return { success: false, message: 'Перемотка недоступна' };
    var time = ymGetTrackTime();
    var parts = String(time.totalTime || '').split(':');
    var duration = parts.reduce(function(total, part) { return total * 60 + Number(part); }, 0);
    if (!Number.isFinite(duration) || duration <= 0) return { success: false, message: 'Неизвестна длительность трека' };
    var newValue = Math.max(0, Math.min(maxValue, currentValue + deltaSeconds * maxValue / duration));
    ymSetRangeValue(progressSlider, newValue);
    return {
      success: true,
      oldPosition: currentValue,
      newPosition: newValue,
      max: maxValue,
      delta: deltaSeconds
    };
  } catch (err) {
    return { success: false, message: err.message, error: err.toString() };
  }
}


function ymFindVibeContextMenuButton() {
  var bar = ymFindVibePlayerBar();
  if (!bar) return null;
  return bar.querySelector('[data-test-id="VIBE_CONTEXT_MENU_BUTTON"]')
    || bar.querySelector('button[aria-label="Контекстное меню"]')
    || bar.querySelector('button[aria-haspopup="menu"]');
}

function ymFindVibeContextMenuPanel() {
  var button = ymFindVibeContextMenuButton();
  if (!button || button.getAttribute('aria-expanded') !== 'true') return null;
  var controlsId = button.getAttribute('aria-controls');
  var panel = controlsId ? document.getElementById(controlsId) : null;
  if (panel) return panel;
  var menus = document.querySelectorAll('[role="menu"]');
  for (var i = 0; i < menus.length; i++) {
    if (menus[i].getAttribute('aria-labelledby') === button.id && button.id) return menus[i];
    if (menus[i].querySelector('[data-test-id="VIBE_CONTEXT_MENU"]')) return menus[i];
  }
  return null;
}

function ymCloseVibeContextMenu() {
  var btn = ymFindVibeContextMenuButton();
  if (btn && btn.getAttribute('aria-expanded') === 'true') {
    btn.click();
    return true;
  }
  return false;
}

function ymFindMenuButtonByIconFragment(fragment, scope) {
  if (scope && scope.getAttribute && scope.getAttribute('role') === 'menu') {
    var vibeItem = ymFindVibeMenuItem(fragment, scope);
    if (vibeItem) return vibeItem;
  }
  scope = scope || document;
  var buttons = scope.querySelectorAll('button, [role="menuitem"]');
  for (var i = 0; i < buttons.length; i++) {
    var b = buttons[i];
    var useEl = b.querySelector('use');
    var href = useEl
      ? (useEl.getAttribute('xlink:href') || useEl.getAttribute('href') || '')
      : '';
    var label = (b.getAttribute('aria-label') || b.textContent || '').toLowerCase();

    if (fragment === 'shuffle') {
      if (href.indexOf('shuffle_xxs') !== -1 || href.indexOf('shuffle') !== -1) return b;
      if (label.indexOf('перемеш') !== -1 || label.indexOf('случай') !== -1) return b;
    }

    if (fragment === 'repeat') {
      if ((href.indexOf('repeat_one') !== -1 || href.indexOf('repeat_xxs') !== -1 || href.indexOf('repeat') !== -1) && href.indexOf('shuffle') === -1) return b;
      if (label.indexOf('повтор') !== -1) return b;
    }
  }
  return null;
}

function ymIsVibeMenuItemActive(button) {
  if (!button) return false;
  return button.className.indexOf('VibeContextMenu_item_active') !== -1;
}

function ymFindVibeMenuItem(fragment, scope) {
  if (!scope) return null;
  var direct = scope.querySelector('[data-test-id="VIBE_CONTEXT_MENU_' + fragment.toUpperCase() + '_ITEM"]');
  if (direct) return direct;
  var items = scope.querySelectorAll('[role="menuitem"]');
  for (var i = 0; i < items.length; i++) {
    var b = items[i];
    var useEl = b.querySelector('use');
    var href = useEl
      ? (useEl.getAttribute('xlink:href') || useEl.getAttribute('href') || '')
      : '';
    var label = (b.textContent || '').toLowerCase();
    if (fragment === 'shuffle' && (href.indexOf('shuffle_xxs') !== -1 || label.indexOf('перемеш') !== -1)) return b;
    if (fragment === 'repeat' && (href.indexOf('repeat_one_xxs') !== -1 || href.indexOf('repeat_xxs') !== -1 || label.indexOf('повтор') !== -1)) return b;
  }
  return null;
}

function ymReadVibeMenuShuffleOn(button) {
  return ymIsVibeMenuItemActive(button);
}

function ymReadVibeMenuRepeatMode(button) {
  if (!button || !ymIsVibeMenuItemActive(button)) return 0;
  var useEl = button.querySelector('use');
  var href = useEl
    ? (useEl.getAttribute('xlink:href') || useEl.getAttribute('href') || '')
    : '';
  if (href.indexOf('repeat_one') !== -1) return 2;
  return 1;
}

function ymDetectVibeShuffleAvailable() {
  if (!ymIsVibePageActive()) return null;
  var panel = ymFindVibeContextMenuPanel();
  return panel ? !!ymFindVibeMenuItem('shuffle', panel) : null;
}

function ymScanVibeContextMenu(panel) {
  if (!panel) return null;
  var shuffleBtn = ymFindVibeMenuItem('shuffle', panel);
  var repeatBtn = ymFindVibeMenuItem('repeat', panel);
  return {
    shuffleAvailable: !!shuffleBtn,
    shuffleOn: shuffleBtn ? ymReadVibeMenuShuffleOn(shuffleBtn) : null,
    repeatMode: repeatBtn ? ymReadVibeMenuRepeatMode(repeatBtn) : null
  };
}

function ymReadRepeatModeFromButton(button) {
  if (!button) return 0;
  if (button.getAttribute('role') === 'menuitem') {
    return ymReadVibeMenuRepeatMode(button);
  }
  var useEl = button.querySelector('use');
  var href = useEl
    ? (useEl.getAttribute('xlink:href') || useEl.getAttribute('href') || '')
    : '';
  if (href.indexOf('repeat_one') !== -1 && ymIsVibeMenuItemActive(button)) return 2;
  if (ymIsVibeMenuItemActive(button)) return 1;
  if (href.indexOf('repeat_one') !== -1) return 2;
  if (button.getAttribute('aria-pressed') === 'true') return 1;
  return 0;
}

async function ymOpenVibeContextMenu() {
  var btn = ymFindVibeContextMenuButton();
  if (!ymCanClick(btn)) return { success: false, message: 'Кнопка контекстного меню недоступна' };

  if (btn.getAttribute('aria-expanded') !== 'true') {
    btn.click();
    for (var i = 0; i < 12; i++) {
      await ymWait(50);
      if (ymFindVibeContextMenuPanel()) {
        return { success: true, message: 'Меню открыто' };
      }
    }
    return { success: false, message: 'Контекстное меню не появилось' };
  }

  return { success: true, message: 'Меню уже открыто' };
}

function ymPublishVibeMenuState(snapshot) {
  if (!snapshot) return;
  var previous = window.__YM_AJAZZ_STATE || {};
  window.__YM_AJAZZ_STATE = Object.assign({}, previous, snapshot, {
    vibeActive: true,
    version: (previous.version || 0) + 1
  });
  if (typeof ymAjazzNotify === 'function') ymAjazzNotify(JSON.stringify(window.__YM_AJAZZ_STATE));
}

async function ymToggleVibeMenuControl(fragment) {
  if (!ymIsVibePageActive()) return null;
  var toggle = ymFindVibeContextMenuButton();
  var wasOpen = toggle && toggle.getAttribute('aria-expanded') === 'true';
  try {
    var opened = await ymOpenVibeContextMenu();
    if (!opened.success) return opened;
    var panel = ymFindVibeContextMenuPanel();
    var button = panel ? ymFindVibeMenuItem(fragment, panel) : null;
    if (!ymCanClick(button)) return { success: false, message: 'Управление недоступно', unavailable: true };
    var before = fragment === 'shuffle' ? ymReadVibeMenuShuffleOn(button) : ymReadVibeMenuRepeatMode(button);
    button.click();
    var actual = null;
    var snapshot = null;
    var deadline = Date.now() + 1600;
    for (var attempt = 0; attempt < 20 && Date.now() < deadline; attempt++) {
      await ymWait(40);
      panel = ymFindVibeContextMenuPanel();
      if (!panel) {
        var reopened = await ymOpenVibeContextMenu();
        if (!reopened.success) break;
        panel = ymFindVibeContextMenuPanel();
      }
      snapshot = ymScanVibeContextMenu(panel);
      var value = snapshot ? (fragment === 'shuffle' ? snapshot.shuffleOn : snapshot.repeatMode) : null;
      if (value !== null && value !== before) { actual = value; break; }
    }
    if (actual !== null) ymPublishVibeMenuState(snapshot);
    var result = { success: true, accepted: true, confirmed: actual !== null };
    if (actual !== null) result.state = snapshot;
    if (fragment === 'shuffle') result.shuffle = actual;
    else result.mode = actual;
    return result;
  } finally {
    if (!wasOpen) ymCloseVibeContextMenu();
  }
}

function ymDetectSonataControlByFragment(fragment) {
  var bar = ymFindSonataPlayerBar();
  if (!bar) return { ok: false };
  var buttons = bar.querySelectorAll('button');
  for (var i = 0; i < buttons.length; i++) {
    var b = buttons[i];
    var useEl = b.querySelector('use');
    if (!useEl) continue;
    var href = useEl.getAttribute('xlink:href') || useEl.getAttribute('href') || '';
    if (fragment === 'shuffle' && href.indexOf('shuffle') !== -1) {
      return { ok: true, shuffle: b.getAttribute('aria-pressed') === 'true' };
    }
    if (fragment === 'repeat' && href.indexOf('repeat') !== -1 && href.indexOf('shuffle') === -1) {
      return { ok: true, mode: ymReadRepeatModeFromButton(b) };
    }
  }
  return { ok: false };
}

function ymDetectVibeMenuControlState(fragment) {
  if (!ymIsVibePageActive()) return { ok: false };
  var panel = ymFindVibeContextMenuPanel();
  if (!panel) return { ok: false };
  var menuBtn = ymFindVibeMenuItem(fragment, panel);
  if (!menuBtn) return { ok: false, unavailable: fragment === 'shuffle' };
  if (fragment === 'shuffle') {
    return { ok: true, shuffle: ymReadVibeMenuShuffleOn(menuBtn) };
  }
  return { ok: true, mode: ymReadVibeMenuRepeatMode(menuBtn) };
}

async function ymToggleSonataControlByFragment(fragment) {
  var bar = ymFindSonataPlayerBar();
  if (!bar) return { success: false, message: 'Нет панели плеера' };
  var buttons = bar.querySelectorAll('button');
  for (var i = 0; i < buttons.length; i++) {
    var b = buttons[i];
    var useEl = b.querySelector('use');
    if (!useEl) continue;
    var href = useEl.getAttribute('xlink:href') || useEl.getAttribute('href') || '';
    if (href.indexOf(fragment) !== -1 && !(fragment === 'repeat' && href.indexOf('shuffle') !== -1)) {
      if (!ymCanClick(b)) return { success: false, message: 'Управление недоступно' };
      var before = ymDetectSonataControlByFragment(fragment);
      b.click();
      var expected = fragment === 'shuffle' ? !before.shuffle : (before.mode + 1) % 3;
      var actual = await ymWaitForState(function() {
        var value = ymDetectSonataControlByFragment(fragment);
        if (!value.ok) return null;
        return fragment === 'shuffle' ? value.shuffle : value.mode;
      }, before.ok ? expected : undefined);
      var result = { success: true, accepted: true, confirmed: actual !== null };
      if (fragment === 'shuffle') result.shuffle = actual;
      else result.mode = actual;
      return result;
    }
  }
  return { success: false, message: 'Кнопка ' + fragment + ' не найдена' };
}

async function ymToggleShuffle() {
  if (ymIsVibePageActive()) {
    var vibeResult = await ymToggleVibeMenuControl('shuffle');
    return vibeResult;
  }
  return ymToggleSonataControlByFragment('shuffle');
}

async function ymToggleRepeat() {
  if (ymIsVibePageActive()) {
    var vibeResult = await ymToggleVibeMenuControl('repeat');
    return vibeResult;
  }
  return ymToggleSonataControlByFragment('repeat');
}

function ymDetectShufflePressed() {
  if (ymIsVibePageActive()) {
    var observed = ymDetectVibeMenuControlState('shuffle');
    if (observed.ok) return observed;
  }
  if (window.__YM_AJAZZ_STATE && window.__YM_AJAZZ_STATE.vibeActive) {
    if (window.__YM_AJAZZ_STATE.shuffleAvailable === false) {
      return { ok: true, shuffle: false, unavailable: true };
    }
    if (window.__YM_AJAZZ_STATE.shuffleOn !== null && window.__YM_AJAZZ_STATE.shuffleOn !== undefined) {
      return { ok: true, shuffle: !!window.__YM_AJAZZ_STATE.shuffleOn };
    }
  }
  if (!ymIsVibePageActive()) return ymDetectSonataControlByFragment('shuffle');
  if (ymIsVibePageActive()) {
    var vibeOpen = ymDetectVibeMenuControlState('shuffle');
    if (vibeOpen.ok) return vibeOpen;
    if (ymDetectVibeShuffleAvailable() === false) {
      return { ok: true, shuffle: false, unavailable: true };
    }
  }
  return { ok: false };
}

function ymDetectRepeatMode() {
  if (ymIsVibePageActive()) {
    var observed = ymDetectVibeMenuControlState('repeat');
    if (observed.ok) return observed;
  }
  if (window.__YM_AJAZZ_STATE && window.__YM_AJAZZ_STATE.vibeActive && window.__YM_AJAZZ_STATE.repeatMode !== null && window.__YM_AJAZZ_STATE.repeatMode !== undefined) {
    return { ok: true, mode: window.__YM_AJAZZ_STATE.repeatMode };
  }
  if (!ymIsVibePageActive()) return ymDetectSonataControlByFragment('repeat');
  if (ymIsVibePageActive()) {
    var vibeOpen = ymDetectVibeMenuControlState('repeat');
    if (vibeOpen.ok) return vibeOpen;
  }
  return { ok: false };
}


function ymCollectAjazzState() {
  var metadata = ymGetTrackInfo();
  var time = ymGetTrackTime();
  var vibe = ymIsVibePageActive();
  var menu = vibe ? ymScanVibeContextMenu(ymFindVibeContextMenuPanel()) : null;
  var shuffle = vibe ? null : ymDetectSonataControlByFragment('shuffle');
  var repeat = vibe ? null : ymDetectSonataControlByFragment('repeat');
  var next = {
    playerReady: ymIsMusicPlayerReady(),
    vibeActive: vibe,
    shuffleAvailable: vibe ? ymDetectVibeShuffleAvailable() : true,
    shuffleOn: menu ? menu.shuffleOn : (shuffle && shuffle.ok ? shuffle.shuffle : null),
    repeatMode: menu ? menu.repeatMode : (repeat && repeat.ok ? repeat.mode : null),
    trackTitle: metadata.success ? metadata.title : null,
    trackArtist: metadata.success ? metadata.artist : null,
    trackUrl: metadata.success ? metadata.trackUrl : null,
    coverUrl: metadata.success ? metadata.coverUrl : null,
    playing: ymDetectPlaybackIsPlaying(),
    liked: ymDetectLikeIsLiked(),
    muted: ymDetectMuteIsMuted(),
    currentTime: time.success ? time.currentTime : null,
    totalTime: time.success ? time.totalTime : null,
    progressValue: time.success ? time.progressValue : null,
    progressMax: time.success ? time.progressMax : null,
    version: 0
  };
  return next;
}

function ymStateFingerprint(state) {
  var fields = ['playerReady', 'vibeActive', 'shuffleAvailable', 'shuffleOn', 'repeatMode', 'trackTitle',
    'trackArtist', 'trackUrl', 'coverUrl', 'playing', 'liked', 'muted', 'currentTime', 'totalTime'];
  return JSON.stringify(fields.map(function(key) { return state[key]; }));
}

function ymInstallAjazzObserver() {
  if (window.__YM_AJAZZ_OBSERVER__ && window.__YM_AJAZZ_OBSERVER__.dispose) {
    window.__YM_AJAZZ_OBSERVER__.dispose();
  }
  var timer = null;
  var observedRoot = null;
  var observedMenu = null;
  var disposed = false;
  var lastFingerprint = '';
  var observer = new MutationObserver(scheduleRefresh);
  var menuObserver = new MutationObserver(scheduleRefresh);
  var rootObserver = new MutationObserver(function() {
    var previousRoot = observedRoot;
    var previousMenu = observedMenu;
    attachPlayerObserver();
    if (previousRoot !== observedRoot || previousMenu !== observedMenu) scheduleRefresh();
  });

  function publish() {
    if (typeof ymAjazzNotify === 'function') ymAjazzNotify(JSON.stringify(window.__YM_AJAZZ_STATE));
  }

  function refresh() {
    if (disposed) return;
    var previous = window.__YM_AJAZZ_STATE || {};
    var next = ymCollectAjazzState();
    var sameTrack = next.trackTitle && next.trackTitle === previous.trackTitle
      && next.trackArtist === previous.trackArtist;
    if (next.vibeActive === previous.vibeActive) {
      if (next.shuffleAvailable === null) next.shuffleAvailable = previous.shuffleAvailable ?? null;
      if (next.shuffleOn === null) next.shuffleOn = previous.shuffleOn ?? null;
      if (next.repeatMode === null) next.repeatMode = previous.repeatMode ?? null;
    }
    if (sameTrack) {
      if (!next.trackUrl) next.trackUrl = previous.trackUrl;
      if (!next.coverUrl) next.coverUrl = previous.coverUrl;
    }
    next.version = (previous.version || 0) + 1;
    var fingerprint = ymStateFingerprint(next);
    if (fingerprint !== lastFingerprint) {
      lastFingerprint = fingerprint;
      window.__YM_AJAZZ_STATE = next;
      publish();
    }
  }

  function scheduleRefresh() {
    if (disposed || timer) return;
    timer = setTimeout(function() { timer = null; refresh(); }, 80);
  }

  function attachPlayerObserver() {
    if (disposed) return;
    var menu = ymFindVibeContextMenuPanel();
    if (menu !== observedMenu) {
      menuObserver.disconnect();
      observedMenu = menu;
      if (menu) menuObserver.observe(menu, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['class', 'aria-pressed', 'aria-disabled'] });
    }
    var root = ymIsVibePageActive()
      ? (ymFindVibePlayerBar() || ymFindVibeMetadataRoot())
      : ymFindSonataPlayerBar();
    if (root === observedRoot) return;
    observer.disconnect();
    observedRoot = root;
    if (!root) return;
    observer.observe(root, {
      childList: true,
      characterData: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['aria-expanded', 'aria-pressed', 'aria-label', 'aria-hidden', 'class', 'disabled', 'src', 'srcset']
    });
  }

  var interval = setInterval(function() { attachPlayerObserver(); scheduleRefresh(); }, 2000);
  window.__YM_AJAZZ_OBSERVER__ = {
    dispose: function() {
      if (disposed) return;
      disposed = true;
      clearTimeout(timer);
      clearInterval(interval);
      observer.disconnect();
      menuObserver.disconnect();
      rootObserver.disconnect();
      window.__YM_AJAZZ_OBSERVER__ = null;
    }
  };
  if (document.body || document.documentElement) {
    rootObserver.observe(document.body || document.documentElement, { childList: true, subtree: true });
  }
  attachPlayerObserver();
  refresh();
}


ymInstallMediaPositionCapture();
window.__YM_BRIDGE_CDP_HELPERS_READY__ = true;
