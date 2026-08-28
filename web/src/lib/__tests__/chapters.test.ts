import { describe, expect, it } from "vitest";

import {
  RESTART_WINDOW_SECONDS,
  chapterIndexAt,
  nextChapterTarget,
  previousChapterTarget,
  sponsorSkipTarget,
} from "../chapters";

const chapters = [
  { start_seconds: 0, title: null, sponsor: false },
  { start_seconds: 100, title: "Two", sponsor: false },
  { start_seconds: 200, title: "Three", sponsor: false },
];

describe("which chapter is playing", () => {
  it("is the last one that has started", () => {
    expect(chapterIndexAt(chapters, 0)).toBe(0);
    expect(chapterIndexAt(chapters, 99)).toBe(0);
    expect(chapterIndexAt(chapters, 100)).toBe(1);
    expect(chapterIndexAt(chapters, 250)).toBe(2);
  });

  it("is nothing when the playhead sits before the first chapter", () => {
    expect(chapterIndexAt([{ start_seconds: 30, title: "Late", sponsor: false }], 10)).toBe(-1);
  });
});

describe("previous chapter", () => {
  it("restarts the current chapter when you are into it", () => {
    // Pressing back once replays what you are in the middle of; twice leaves. Without
    // this, a chapter you are ten minutes into cannot be restarted except by scrubbing.
    expect(previousChapterTarget(chapters, 150)).toBe(100);
  });

  it("goes back one when you are at the very start of a chapter", () => {
    expect(previousChapterTarget(chapters, 100 + RESTART_WINDOW_SECONDS - 1)).toBe(0);
  });

  it("stays put at the start of the first chapter", () => {
    expect(previousChapterTarget(chapters, 1)).toBe(0);
  });

  it("has nowhere to go before the first chapter begins", () => {
    expect(previousChapterTarget([{ start_seconds: 30, title: "Late", sponsor: false }], 10)).toBeNull();
  });
});

describe("next chapter", () => {
  it("goes to the next one that has not started", () => {
    expect(nextChapterTarget(chapters, 0)).toBe(100);
    expect(nextChapterTarget(chapters, 150)).toBe(200);
  });

  it("is null in the last chapter, so the control can be disabled", () => {
    expect(nextChapterTarget(chapters, 250)).toBeNull();
  });

  it("does not treat the chapter you are exactly on as next", () => {
    expect(nextChapterTarget(chapters, 100)).toBe(200);
  });
});

describe("skipping sponsor breaks", () => {
  const withAds = [
    { start_seconds: 0, title: "Intro", sponsor: false },
    { start_seconds: 100, title: "Sponsor: Acme", sponsor: true },
    { start_seconds: 160, title: "Sponsor: Globex", sponsor: true },
    { start_seconds: 220, title: "Interview", sponsor: false },
  ];

  it("jumps past a whole run of ads, not just the first", () => {
    // Shows commonly place two or three back to back, and stopping at the first would
    // land you in the second one.
    expect(sponsorSkipTarget(withAds, 120)).toBe(220);
    expect(sponsorSkipTarget(withAds, 170)).toBe(220);
  });

  it("leaves the playhead alone outside a break", () => {
    expect(sponsorSkipTarget(withAds, 10)).toBeNull();
    expect(sponsorSkipTarget(withAds, 300)).toBeNull();
  });

  it("does not skip a trailing ad with nothing after it", () => {
    // There would be no end to jump to, and seeking to the end of the file would count as
    // finishing the episode.
    const trailing = [
      { start_seconds: 0, title: "Talk", sponsor: false },
      { start_seconds: 90, title: "Sponsor", sponsor: true },
    ];

    expect(sponsorSkipTarget(trailing, 95)).toBeNull();
  });

  it("does nothing for a show with no chapters", () => {
    expect(sponsorSkipTarget([], 42)).toBeNull();
  });
});
