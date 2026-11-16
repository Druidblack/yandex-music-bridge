(function installYandexMusicHaObserver() {
  if (window.self !== window.top) return;
  if (window.__YM_BRIDGE_DESKTOP_OBSERVER__?.dispose) {
    window.__YM_BRIDGE_DESKTOP_OBSERVER__.dispose();
  }

  let disposed = false;
  let timer = null;
  let observedRoot = null;
  let observedMenu = null;
  let fingerprint = '';
  const observer = new MutationObserver(scheduleRefresh);
  const menuObserver = new MutationObserver(scheduleRefresh);
  const rootObserver = new MutationObserver(function() {
    const oldRoot = observedRoot;
    const oldMenu = observedMenu;
    attachPlayerObserver();
    if (oldRoot !== observedRoot || oldMenu !== observedMenu) scheduleRefresh();
  });

  function collect() {
    const previous = window.__YM_BRIDGE_DESKTOP_STATE__ || {};
    const next = ymCollectAjazzState();
    const volume = ymGetVolume();
    next.volumePercent = volume && volume.success ? volume.volume : null;
    const sameTrack = next.trackTitle && next.trackTitle === previous.trackTitle
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
    return next;
  }

  function refresh() {
    if (disposed) return;
    try {
      const next = collect();
      const signature = ymStateFingerprint(next) + '|' + next.volumePercent;
      if (signature === fingerprint) return;
      fingerprint = signature;
      window.__YM_BRIDGE_DESKTOP_STATE__ = next;
      if (typeof window.ymBridgeCdpNotify === 'function') {
        window.ymBridgeCdpNotify(JSON.stringify(next));
      }
    } catch (err) {
      // A partial re-render can invalidate player nodes. Retry shortly.
      scheduleRefresh();
    }
  }

  function scheduleRefresh() {
    if (disposed || timer !== null) return;
    timer = setTimeout(function() {
      timer = null;
      refresh();
    }, 80);
  }

  function attachPlayerObserver() {
    if (disposed) return;
    const menu = ymFindVibeContextMenuPanel();
    if (menu !== observedMenu) {
      menuObserver.disconnect();
      observedMenu = menu;
      if (menu) menuObserver.observe(menu, {
        childList: true, subtree: true, characterData: true, attributes: true,
        attributeFilter: ['class', 'aria-pressed', 'aria-disabled']
      });
    }
    const root = ymIsVibePageActive()
      ? (ymFindVibePlayerBar() || ymFindVibeMetadataRoot())
      : ymFindSonataPlayerBar();
    if (root === observedRoot) return;
    observer.disconnect();
    observedRoot = root;
    if (root) observer.observe(root, {
      childList: true, characterData: true, subtree: true, attributes: true,
      attributeFilter: ['aria-expanded', 'aria-pressed', 'aria-label', 'aria-hidden',
        'class', 'disabled', 'src', 'srcset', 'value']
    });
  }

  const interval = setInterval(function() {
    attachPlayerObserver();
    scheduleRefresh();
  }, 2000);
  window.__YM_BRIDGE_DESKTOP_OBSERVER__ = {
    dispose: function() {
      if (disposed) return;
      disposed = true;
      clearTimeout(timer);
      clearInterval(interval);
      observer.disconnect();
      menuObserver.disconnect();
      rootObserver.disconnect();
      window.__YM_BRIDGE_DESKTOP_OBSERVER__ = null;
    }
  };
  if (document.body || document.documentElement) {
    rootObserver.observe(document.body || document.documentElement, {
      childList: true, subtree: true
    });
  }
  attachPlayerObserver();
  refresh();
})();
