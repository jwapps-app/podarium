import { describe, expect, it } from "vitest";

import { isNewArrival } from "../newness";
import type { Episode, Feed } from "../types";

const HOUR = 3_600_000;

function episode(firstSeenAt: Date): Episode {
  return { id: 1, feed_id: 1, first_seen_at: firstSeenAt.toISOString() } as Episode;
}

function feed(createdAt: Date): Feed {
  return { id: 1, created_at: createdAt.toISOString() } as Feed;
}

describe("isNewArrival", () => {
  it("flags an episode that arrived after the feed was already subscribed", () => {
    const subscribedLastWeek = new Date(Date.now() - 7 * 24 * HOUR);
    const arrivedAnHourAgo = new Date(Date.now() - HOUR);

    expect(isNewArrival(episode(arrivedAnHourAgo), feed(subscribedLastWeek))).toBe(true);
  });

  it("does not flag the initial backlog of a freshly subscribed feed", () => {
    // Subscribing to a show with thousands of episodes gives every one of them the same
    // very recent first_seen_at. Tagging all of them "new" is true and useless.
    const justSubscribed = new Date(Date.now() - 30_000);

    expect(isNewArrival(episode(justSubscribed), feed(justSubscribed))).toBe(false);
  });

  it("does not flag anything older than a day", () => {
    const subscribedLongAgo = new Date(Date.now() - 30 * 24 * HOUR);
    const arrivedTwoDaysAgo = new Date(Date.now() - 48 * HOUR);

    expect(isNewArrival(episode(arrivedTwoDaysAgo), feed(subscribedLongAgo))).toBe(false);
  });

  it("falls back to the recency check when the feed is not loaded yet", () => {
    expect(isNewArrival(episode(new Date(Date.now() - HOUR)), undefined)).toBe(true);
  });
});
