import type { Episode, Feed } from "./types";

const DAY_MS = 86_400_000;

/** Episodes first seen within this long of a feed being added are its initial backlog,
 *  not new arrivals. Generous enough to cover a slow first fetch of a large feed. */
const BACKFILL_WINDOW_MS = 120_000;

/** Whether an episode should be flagged as newly arrived.
 *
 *  Keyed off first_seen_at, never published_at -- a publisher re-stamping pubDate across
 *  its back catalogue must not light up the whole inbox.
 *
 *  The subtlety is the other direction. Subscribing to a show with 3,000 episodes gives
 *  every one of them the same, very recent, first_seen_at, because they genuinely were
 *  all first seen just now. Tagging all 3,000 "new" is true and useless. So an episode
 *  only counts as new if it turned up *after* the feed's initial import.
 */
export function isNewArrival(episode: Episode, feed: Feed | undefined): boolean {
  const firstSeen = new Date(episode.first_seen_at).getTime();
  if (!Number.isFinite(firstSeen)) return false;

  if (Date.now() - firstSeen >= DAY_MS) return false;

  if (feed) {
    const feedAdded = new Date(feed.created_at).getTime();
    if (Number.isFinite(feedAdded) && firstSeen - feedAdded < BACKFILL_WINDOW_MS) {
      return false;
    }
  }

  return true;
}
