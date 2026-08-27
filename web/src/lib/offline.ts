/** Saving episodes to the device, via the service worker.
 *
 *  Everything here degrades to "unsupported" rather than throwing: a browser without
 *  service workers, or a page served over plain HTTP where they are unavailable, should
 *  quietly not offer the feature instead of erroring in the middle of the library.
 */

export const offlineSupported = () =>
  typeof navigator !== "undefined" && "serviceWorker" in navigator && window.isSecureContext;

/** Ask the worker something and wait for its reply.
 *
 *  postMessage has no request/response pairing of its own, so each call listens for the
 *  next message naming the same episode and gives up after a while -- a save that never
 *  answers must not leave a spinner running forever.
 */
function ask<T>(message: object, matches: (data: any) => boolean, timeoutMs = 300_000): Promise<T> {
  return new Promise((resolve, reject) => {
    const worker = navigator.serviceWorker.controller;
    if (!worker) {
      reject(new Error("The offline worker is not running yet. Reload and try again."));
      return;
    }

    const timer = window.setTimeout(() => {
      navigator.serviceWorker.removeEventListener("message", onMessage);
      reject(new Error("The download timed out."));
    }, timeoutMs);

    const onMessage = (event: MessageEvent) => {
      if (!matches(event.data)) return;
      window.clearTimeout(timer);
      navigator.serviceWorker.removeEventListener("message", onMessage);
      if (event.data?.type?.endsWith("failed")) {
        reject(new Error(event.data.message || "Could not save the episode."));
      } else {
        resolve(event.data as T);
      }
    };

    navigator.serviceWorker.addEventListener("message", onMessage);
    worker.postMessage(message);
  });
}

export async function saveEpisode(id: number): Promise<void> {
  await ask({ type: "save-episode", id }, (data) => data?.id === id && data?.type !== "saved-list");
}

export async function forgetEpisode(id: number): Promise<void> {
  await ask({ type: "forget-episode", id }, (data) => data?.type === "forgotten" && data?.id === id);
}

export async function listSaved(): Promise<number[]> {
  if (!offlineSupported() || !navigator.serviceWorker.controller) return [];
  const reply = await ask<{ ids: number[] }>(
    { type: "list-saved" },
    (data) => data?.type === "saved-list",
    10_000,
  );
  return reply.ids ?? [];
}

/** Register the worker. Failure is not fatal -- the app works, just not offline. */
export async function registerServiceWorker(): Promise<void> {
  if (!offlineSupported()) return;
  try {
    await navigator.serviceWorker.register("/sw.js");
  } catch (cause) {
    console.warn("offline support unavailable", cause);
  }
}

/** Turn a base64url VAPID key into the bytes PushManager.subscribe wants.
 *
 *  applicationServerKey takes a BufferSource. Handing it the string fails with an error
 *  that says nothing about why, which is a memorable afternoon.
 */
export function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=");
  const raw = window.atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
  // Built on an explicit ArrayBuffer: a plain Uint8Array is typed over ArrayBufferLike,
  // which includes SharedArrayBuffer and so is not a BufferSource as far as the DOM types
  // are concerned.
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
  return bytes;
}

/** Serialise a PushSubscription into what the server stores. */
export function describeSubscription(subscription: PushSubscription) {
  const json = subscription.toJSON();
  return {
    endpoint: subscription.endpoint,
    p256dh: json.keys?.p256dh ?? "",
    auth: json.keys?.auth ?? "",
  };
}
