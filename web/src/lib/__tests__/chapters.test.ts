import { describe, expect, it } from "vitest";

import {
  RESTART_WINDOW_SECONDS,
  chapterIndexAt,
  nextChapterTarget,
  previousChapterTarget,
} from "../chapters";

const chapters = [
  { start_seconds: 0, title: null },
  { start_seconds: 100, title: "Two" },
  { start_seconds: 200, title: "Three" },
];

describe("which chapter is playing", () => {
  it("is the last one that has started", () => {
    expect(chapterIndexAt(chapters, 0)).toBe(0);
    expect(chapterIndexAt(chapters, 99)).toBe(0);
    expect(chapterIndexAt(chapters, 100)).toBe(1);
    expect(chapterIndexAt(chapters, 250)).toBe(2);
  });

  it("is nothing when the playhead sits before the first chapter", () => {
    expect(chapterIndexAt([{ start_seconds: 30, title: "Late" }], 10)).toBe(-1);
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
    expect(previousChapterTarget([{ start_seconds: 30, title: "Late" }], 10)).toBeNull();
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
