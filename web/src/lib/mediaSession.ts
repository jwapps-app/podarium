import { useEffect } from "react";

import type { Episode } from "./types";

/** Lock screen and Control Center integration.
 *
 *  Without this the OS has no idea what is playing: no artwork, no title, and on iOS no
 *  transport controls once the screen is locked. The audio element alone is not enough --
 *  Media Session is what hands the platform something to display and something to call.
 */

interface Options {
  episode: Episode | null;
  showTitle: string | null;
  playing: boolean;
  position: number;
  duration: number;
  playbackRate: number;
  resume: () => void;
  pause: () => void;
  skip: (delta: number) => void;
  seek: (seconds: number) => void;
  stop: () => void;
}

interface Transport {
  resume: () => void;
  pause: () => void;
  skip: (delta: number) => void;
  seek: (seconds: number) => void;
  stop: () => void;
}

/** The action table the platform is given.
 *
 *  Extracted and pure so the rule below can be tested: **no handler here may toggle.**
 *  These actions are statements of intent from the OS, not button presses -- "pause" means
 *  pause, and it is delivered in situations that have nothing to do with a user pressing
 *  anything: an interruption ending, a Bluetooth device connecting, CarPlay attaching, the
 *  app returning to the foreground with a restored audio session. A toggle wired here turns
 *  a stray "pause" into playback starting on its own, which is precisely the bug this
 *  shape prevents.
 */
export function mediaSessionHandlers(
  transport: Transport,
): [MediaSessionAction, MediaSessionActionHandler | null][] {
  return [
    ["play", () => transport.resume()],
    ["pause", () => transport.pause()],
    ["stop", () => transport.stop()],
    ["seekbackward", (details) => transport.skip(-(details.seekOffset ?? SKIP_BACK_SECONDS))],
    ["seekforward", (details) => transport.skip(details.seekOffset ?? SKIP_FORWARD_SECONDS)],
    [
      "seekto",
      (details) => {
        if (typeof details.seekTime === "number") transport.seek(details.seekTime);
      },
    ],
    // Explicitly cleared. When these are set the platform shows track-skip buttons
    // instead of the skip-back/skip-forward arcs, and for a podcast the arcs are what
    // you actually reach for. Reaching the end of an episode still advances the queue.
    ["previoustrack", null],
    ["nexttrack", null],
  ];
}

const SKIP_BACK_SECONDS = 10;
const SKIP_FORWARD_SECONDS = 30;

/** The OS fetches these itself, so they have to be absolute. Same-origin, so the session
 *  cookie rides along and /api/images stays behind auth like everything else. */
function artworkFor(episode: Episode): MediaImage[] {
  if (!episode.image_url) return [];
  const href = new URL(episode.image_url, window.location.origin).href;
  // The sizes are a hint for picking a source; ours is one image, so advertise the range
  // rather than lying about a specific dimension.
  return ["256x256", "512x512", "1024x1024"].map((sizes) => ({
    src: href,
    sizes,
    type: "image/jpeg",
  }));
}

export function useMediaSession({
  episode,
  showTitle,
  playing,
  position,
  duration,
  playbackRate,
  resume,
  pause,
  skip,
  seek,
  stop,
}: Options): void {
  const session = typeof navigator !== "undefined" ? navigator.mediaSession : undefined;

  // What the lock screen shows.
  useEffect(() => {
    if (!session) return;
    if (!episode) {
      session.metadata = null;
      return;
    }
    session.metadata = new MediaMetadata({
      title: episode.title ?? "Untitled episode",
      artist: showTitle ?? "Podarium",
      album: showTitle ?? "Podarium",
      artwork: artworkFor(episode),
    });
  }, [session, episode, showTitle]);

  // What its buttons do.
  useEffect(() => {
    if (!session) return;

    const handlers = mediaSessionHandlers({ resume, pause, skip, seek, stop });

    for (const [action, handler] of handlers) {
      try {
        session.setActionHandler(action, handler);
      } catch {
        // Older engines reject actions they do not implement; the rest still apply.
      }
    }

    return () => {
      for (const [action] of handlers) {
        try {
          session.setActionHandler(action, null);
        } catch {
          /* nothing to undo */
        }
      }
    };
  }, [session, resume, pause, skip, seek, stop]);

  useEffect(() => {
    if (!session) return;
    session.playbackState = episode ? (playing ? "playing" : "paused") : "none";
  }, [session, episode, playing]);

  // Drives the lock screen's own scrubber and elapsed time. It runs the clock forward
  // itself from these values, which is why playbackRate has to be included -- at 1.1x an
  // omitted rate would drift visibly against the audio.
  useEffect(() => {
    if (!session?.setPositionState) return;
    if (!episode || !Number.isFinite(duration) || duration <= 0) return;
    try {
      session.setPositionState({
        duration,
        position: Math.min(Math.max(position, 0), duration),
        playbackRate: playbackRate > 0 ? playbackRate : 1,
      });
    } catch {
      // Thrown for inconsistent values mid-seek; the next update corrects it.
    }
  }, [session, episode, position, duration, playbackRate]);
}
