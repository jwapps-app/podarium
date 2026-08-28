/* Podarium service worker: offline shell, saved audio, and push.
 *
 * The design invariant is that the server does every fetch, which makes Podarium useless
 * the moment the phone cannot reach it -- a plane, a dead zone, home internet down. The
 * audio is on docker-audio, not in your pocket. This closes as much of that gap as a web
 * app can: the shell is cached so the app opens, and episodes you explicitly save are
 * stored whole so they play.
 *
 * "Explicitly" is the important part. Caching whatever you happened to stream would fill a
 * phone with things you did not ask for and evict the ones you did -- iOS gives a PWA a
 * bounded budget and reclaims it without warning.
 */

const SHELL = "podarium-shell-v2";
const AUDIO = "podarium-audio-v1";

/** Episode ids held in the audio cache, kept in memory.
 *
 *  The fetch handler has to decide whether to intercept before it is allowed to await
 *  anything, so it cannot ask the cache. Refreshed whenever the cache changes, and once on
 *  activation. Being briefly empty is harmless: a saved episode simply plays from the
 *  network for a moment, which is what would have happened anyway.
 */
const savedIds = new Set();

function episodeIdFrom(pathname) {
  return Number(pathname.split("/").pop());
}

async function refreshSavedIds() {
  const ids = await savedEpisodeIds();
  savedIds.clear();
  ids.forEach((id) => savedIds.add(id));
  return ids;
}

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      // Drop caches from older versions of this file, keep the audio the user saved.
      const names = await caches.keys();
      await Promise.all(
        names
          .filter((name) => name.startsWith("podarium-") && name !== SHELL && name !== AUDIO)
          .map((name) => caches.delete(name)),
      );
      await refreshSavedIds();
      await self.clients.claim();
    })(),
  );
});

/** Save an episode's audio for offline playback. Reports progress to the page. */
async function saveEpisode(id) {
  const cache = await caches.open(AUDIO);
  const url = `/api/stream/${id}`;
  // Range-less, so the response is the whole file and can be sliced later.
  const response = await fetch(url, { credentials: "same-origin" });
  if (!response.ok) throw new Error(`stream returned ${response.status}`);
  await cache.put(url, response.clone());
  savedIds.add(Number(id));
  return true;
}

async function forgetEpisode(id) {
  const cache = await caches.open(AUDIO);
  savedIds.delete(Number(id));
  return cache.delete(`/api/stream/${id}`);
}

async function savedEpisodeIds() {
  const cache = await caches.open(AUDIO);
  const keys = await cache.keys();
  return keys
    .map((request) => Number(new URL(request.url).pathname.split("/").pop()))
    .filter((id) => Number.isFinite(id));
}

self.addEventListener("message", (event) => {
  const { type, id } = event.data || {};
  const reply = (payload) => event.source && event.source.postMessage(payload);

  if (type === "save-episode") {
    event.waitUntil(
      saveEpisode(id)
        .then(() => reply({ type: "saved", id }))
        .catch((error) => reply({ type: "save-failed", id, message: String(error) })),
    );
  } else if (type === "forget-episode") {
    event.waitUntil(forgetEpisode(id).then(() => reply({ type: "forgotten", id })));
  } else if (type === "list-saved") {
    event.waitUntil(refreshSavedIds().then((ids) => reply({ type: "saved-list", ids })));
  }
});

/** Serve a Range request out of a fully-cached response.
 *
 *  An <audio> element always asks for ranges, and the Cache API only ever stores and
 *  returns whole responses -- so without this, a saved episode would be a 200 answering a
 *  question the player did not ask, and seeking would break.
 */
async function rangeFromCache(cached, rangeHeader) {
  const buffer = await cached.arrayBuffer();
  const total = buffer.byteLength;

  const match = /^bytes=(\d*)-(\d*)$/.exec(rangeHeader.trim());
  if (!match) return new Response(null, { status: 416 });

  let start;
  let end;
  if (match[1] === "") {
    // A suffix range: the last N bytes.
    const suffix = Number(match[2]);
    if (!suffix) return new Response(null, { status: 416 });
    start = Math.max(0, total - suffix);
    end = total - 1;
  } else {
    start = Number(match[1]);
    end = match[2] === "" ? total - 1 : Math.min(Number(match[2]), total - 1);
  }

  if (!Number.isFinite(start) || start >= total || end < start) {
    return new Response(null, {
      status: 416,
      headers: { "Content-Range": `bytes */${total}` },
    });
  }

  return new Response(buffer.slice(start, end + 1), {
    status: 206,
    headers: {
      "Content-Type": cached.headers.get("Content-Type") || "audio/mpeg",
      "Content-Length": String(end - start + 1),
      "Content-Range": `bytes ${start}-${end}/${total}`,
      "Accept-Ranges": "bytes",
    },
  });
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Saved audio, served from the cache whether or not there is a network. The point of
  // saving an episode is that it does not depend on reaching the server.
  if (url.pathname.startsWith("/api/stream/")) {
    // Only touched for episodes actually saved here. Everything else is left to the
    // browser's own networking, deliberately.
    //
    // Passing media through a service worker breaks seeking on iOS: a range request that
    // goes out through fetch() and comes back through respondWith stops playback, and
    // dragging past what has loaded is exactly what issues one. Desktop browsers handle
    // the same path without complaint, which is what made this hard to see.
    //
    // The decision has to be synchronous -- respondWith cannot be called after an await --
    // so it reads a set held in memory rather than asking the cache.
    if (!savedIds.has(episodeIdFrom(url.pathname))) return;

    event.respondWith(
      (async () => {
        const cached = await caches.match(url.pathname, { cacheName: AUDIO });
        if (!cached) return fetch(request);
        const range = request.headers.get("Range");
        return range ? rangeFromCache(cached, range) : cached;
      })(),
    );
    return;
  }

  // Vite's dev server. It serves modules from these paths with a fresh query string on
  // every edit, so caching them both fills the cache with dead versions and risks handing
  // back a stale module after a change. None of these paths exist in a built app.
  if (
    url.pathname.startsWith("/@") ||
    url.pathname.startsWith("/src/") ||
    url.pathname.startsWith("/node_modules/")
  ) {
    return;
  }

  // Everything else under /api is live data. Serving a stale library from cache would be
  // worse than an honest failure, so these are never cached.
  if (url.pathname.startsWith("/api/") || url.pathname === "/metrics") return;

  // A navigation offline falls back to the cached shell, so the app opens and can say what
  // it does have rather than showing the browser's error page.
  if (request.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const response = await fetch(request);
          const cache = await caches.open(SHELL);
          cache.put("/", response.clone());
          return response;
        } catch (error) {
          const cached = await caches.match("/", { cacheName: SHELL });
          if (cached) return cached;
          throw error;
        }
      })(),
    );
    return;
  }

  // Static assets: cache-first, because Vite fingerprints their filenames, so a given URL's
  // content never changes and a new build is a new URL.
  event.respondWith(
    (async () => {
      const cached = await caches.match(request, { cacheName: SHELL });
      if (cached) return cached;
      const response = await fetch(request);
      if (response.ok) {
        const cache = await caches.open(SHELL);
        cache.put(request, response.clone());
      }
      return response;
    })(),
  );
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = {};
  }
  event.waitUntil(
    self.registration.showNotification(payload.title || "Podarium", {
      body: payload.body || "",
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      data: { url: payload.url || "/" },
      tag: "podarium-new-episodes",
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of windows) {
        if (client.url.includes(self.location.origin)) {
          await client.focus();
          return client.navigate(target);
        }
      }
      return self.clients.openWindow(target);
    })(),
  );
});
