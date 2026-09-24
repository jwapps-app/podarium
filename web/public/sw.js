/* Podarium service worker: offline shell, saved audio, and push.
 *
 * The design invariant is that the server does every fetch, which makes Podarium useless
 * the moment the phone cannot reach it -- a plane, a dead zone, home internet down. The
 * audio is on the server, not in your pocket. This closes as much of that gap as a web
 * app can: the shell is cached so the app opens, and episodes you explicitly save are
 * stored whole so they play.
 *
 * "Explicitly" is the important part. Caching whatever you happened to stream would fill a
 * phone with things you did not ask for and evict the ones you did -- iOS gives a PWA a
 * bounded budget and reclaims it without warning.
 */

const SHELL = "podarium-shell-v2";
const AUDIO = "podarium-audio-v1";

/** Episode ids held in the audio cache, kept in memory for the page's "what is saved"
 *  question. Not consulted when serving: see the fetch handler. */
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

/** Save an episode's audio for offline playback. Reports progress to the page.
 *
 *  Saved under the URL the player will ask for, query string included: `?v=o` and
 *  `?v=p` name different files, and a cache keyed on the path alone handed the original
 *  back to a player asking for the processed one. */
async function saveEpisode(id, url) {
  const cache = await caches.open(AUDIO);
  const key = url || `/api/stream/${id}`;
  // Range-less, so the response is the whole file and can be sliced later.
  const response = await fetch(key, { credentials: "same-origin" });
  if (!response.ok) throw new Error(`stream returned ${response.status}`);
  // One saved copy per episode, whichever version it is.
  await forgetEpisode(id);
  await cache.put(key, response.clone());
  savedIds.add(Number(id));
  return true;
}

async function forgetEpisode(id) {
  const cache = await caches.open(AUDIO);
  savedIds.delete(Number(id));
  const keys = await cache.keys();
  await Promise.all(
    keys
      .filter((request) => new URL(request.url).pathname === `/api/stream/${id}`)
      .map((request) => cache.delete(request)),
  );
}

async function savedEpisodeIds() {
  const cache = await caches.open(AUDIO);
  const keys = await cache.keys();
  return keys
    .map((request) => Number(new URL(request.url).pathname.split("/").pop()))
    .filter((id) => Number.isFinite(id));
}

self.addEventListener("message", (event) => {
  const { type, id, url } = event.data || {};
  const reply = (payload) => event.source && event.source.postMessage(payload);

  if (type === "save-episode") {
    event.waitUntil(
      saveEpisode(id, url)
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
  // A Blob, not an ArrayBuffer: slicing a Blob is a view onto storage, while arrayBuffer()
  // read the whole episode into memory for every probe and every seek -- a three-hour
  // file, several times over, on a phone.
  const blob = await cached.blob();
  const total = blob.size;

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

  return new Response(blob.slice(start, end + 1), {
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
    // Every stream request is answered from here, and the cache is asked each time.
    //
    // This used to be gated on a set of saved ids held in memory, because respondWith
    // must be called synchronously and the cache cannot be. But the browser stops an
    // idle worker and starts it again later without running activate, and the fresh one
    // knows of nothing saved -- so the gate let the request through to a network that,
    // offline, was not there. The saved episode failed at exactly the moment saving it
    // was for. Calling respondWith with a promise is allowed; only the call itself must
    // be synchronous. The hop this adds for unsaved episodes is a cache miss.
    //
    // (An older note here claimed passing media through a worker broke seeking on iOS.
    // It was a guess made while chasing a seek fault that turned out to be a CSS width.)
    event.respondWith(
      (async () => {
        // The exact version asked for. A saved original is not an answer to a request
        // for the processed file, so that goes to the network like any unsaved episode.
        const cached = await caches.match(url.pathname + url.search, { cacheName: AUDIO });
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
  // The count on the icon, which is the part that survives the notification being
  // dismissed. Carried in the payload rather than counted here: a worker that incremented
  // its own tally would drift the first time a message was dropped, and has no way to find
  // the true figure again. Guarded because badging is a recent addition and absent in
  // browsers -- and, on iOS, in anything that is not an installed home-screen app.
  if (typeof payload.badge === "number" && self.navigator && self.navigator.setAppBadge) {
    event.waitUntil(
      payload.badge > 0
        ? self.navigator.setAppBadge(payload.badge).catch(function () {})
        : self.navigator.clearAppBadge().catch(function () {}),
    );
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
