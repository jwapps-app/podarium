/** Picking an episode up where another device left it.
 *
 *  A player that has been sitting paused knows only where *it* stopped. The episode may
 *  have been carried on elsewhere since -- paused on the desktop, finished in the car --
 *  and a player that resumes from its own old place then reports that place as new, which
 *  takes the other device's progress back with it. So before it resumes, and whenever its
 *  page comes back into view, a paused player asks the server where the episode is.
 *
 *  The decisions are here, apart from the player, so they can be tested without one.
 */

export interface ServerPosition {
  position_seconds: number;
  played: boolean;
  audio_version: string;
  changed_at: string | null;
}

export type Handover = { kind: "keep" } | { kind: "seek"; to: number } | { kind: "unload" };

/** How long after pressing play the server's answer may still move the playhead. The
 *  question is asked as play is pressed, and the answer arrives a moment into playback. */
export const RESUME_GRACE_MS = 8000;

/** Closer than this and the two agree: the last report before a pause can trail the
 *  playhead by a few seconds. */
const AGREEMENT_SECONDS = 5;

export function decideHandover(
  local: { position: number; paused: boolean; resumedAgoMs: number | null },
  server: ServerPosition,
): Handover {
  // A player that is well into playing is the device being listened to. It is the source
  // of the position, not something to be corrected by it.
  const settled =
    !local.paused && (local.resumedAgoMs === null || local.resumedAgoMs > RESUME_GRACE_MS);
  if (settled) return { kind: "keep" };

  if (server.played) return { kind: "unload" };
  // Zero is "never started", not a place to go back to: following it would undo an
  // intro skip on an episode that was cued and has not been played yet.
  if (server.position_seconds === 0) return { kind: "keep" };
  if (Math.abs(server.position_seconds - local.position) > AGREEMENT_SECONDS) {
    return { kind: "seek", to: server.position_seconds };
  }
  return { kind: "keep" };
}

/** Whether a closing page has anything the server has not already been told.
 *
 *  A page that has only been sitting paused has nothing: its position went up when it
 *  paused. Sending it again, stamped with the time of closing, is how reloading a tab on
 *  one machine used to overwrite an hour of listening done on another.
 */
export function finalWriteNeeded(
  position: number,
  lastReported: number,
  unsentListenedSeconds: number,
): boolean {
  return Math.floor(position) !== lastReported || Math.floor(unsentListenedSeconds) > 0;
}
