/** Not outliving an update.
 *
 *  A tab left open across a server update goes on running the code it loaded, against a
 *  server that has moved on, until someone reloads it. That is not only a matter of
 *  missing new features: old code keeps its old bugs, and one of them -- a closing page
 *  rewriting the playback position -- went on doing damage from an open tab after it had
 *  been fixed. So a page that comes back into view asks the server which build it is
 *  handing out now, and reloads itself if that is no longer the one it is running.
 *
 *  Only when nothing would be lost by it: not while audio is playing, not while someone
 *  is typing. And once per build, so a server that cannot hand over the new one does not
 *  leave the page reloading in a loop.
 */

const BUNDLE = /\/assets\/(index-[A-Za-z0-9_-]+\.js)/;
const TRIED_KEY = "podarium.reloadedFor";
/** A deploy is rare; asking on every glance at the tab is not worth a request each. */
const ASK_EVERY_MS = 60_000;

/** The name of the script this page is running, or null in development, where there is
 *  no build to be behind. */
export function bundleNameFrom(scriptUrl: string): string | null {
  const match = BUNDLE.exec(scriptUrl);
  return match ? match[1] : null;
}

export function shouldReload(state: {
  own: string | null;
  latest: string | null;
  playing: boolean;
  typing: boolean;
  alreadyTriedFor: string | null;
}): boolean {
  if (!state.own || !state.latest) return false;
  if (state.own === state.latest) return false;
  // Reloaded for this build once already and still not running it: something between
  // here and the server is holding the old one. Trying again would only loop.
  if (state.alreadyTriedFor === state.latest) return false;
  return !state.playing && !state.typing;
}

/** Whether a reload would throw away something half-written. A field that merely has
 *  the cursor in it does not count: the sign-in screen and the search box both take
 *  focus by themselves, and an empty field has nothing to lose. */
function isTyping(): boolean {
  const active = document.activeElement;
  if (!active) return false;
  if (active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) {
    return active.value.length > 0;
  }
  return (active as HTMLElement).isContentEditable && (active.textContent ?? "").length > 0;
}

function isPlaying(): boolean {
  return Array.from(document.querySelectorAll("audio")).some((audio) => !audio.paused);
}

function readTried(): string | null {
  try {
    return window.sessionStorage.getItem(TRIED_KEY);
  } catch {
    return null;
  }
}

/** Start watching. Call once, at startup. */
export function stayFresh(): void {
  const own = bundleNameFrom(import.meta.url);
  if (!own) return;

  let lastAsked = 0;
  const check = async () => {
    if (document.visibilityState !== "visible") return;
    const now = Date.now();
    if (now - lastAsked < ASK_EVERY_MS) return;
    lastAsked = now;

    let latest: string | null = null;
    try {
      const response = await fetch("/api/version", { cache: "no-store" });
      if (!response.ok) return;
      latest = ((await response.json()) as { web?: string | null }).web ?? null;
    } catch {
      return;
    }

    if (
      shouldReload({
        own,
        latest,
        playing: isPlaying(),
        typing: isTyping(),
        alreadyTriedFor: readTried(),
      })
    ) {
      try {
        window.sessionStorage.setItem(TRIED_KEY, latest!);
      } catch {
        // Without the note there is no loop guard, so do not risk the loop.
        return;
      }
      window.location.reload();
    }
  };

  document.addEventListener("visibilitychange", () => void check());
  window.addEventListener("pageshow", () => void check());
  window.addEventListener("focus", () => void check());
}
