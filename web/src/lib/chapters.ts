import type { Chapter } from "./types";

/** How long into a chapter "previous" means restart it rather than go back one.
 *
 *  Borrowed from every music player there has ever been, and it is the behaviour people
 *  already expect from the gesture: pressing back once replays what you are in the middle
 *  of, pressing it twice leaves. Without the window, a chapter you are ten minutes into
 *  cannot be restarted at all except by scrubbing.
 */
export const RESTART_WINDOW_SECONDS = 3;

/** Index of the chapter containing a position: the last one that has started. */
export function chapterIndexAt(chapters: Chapter[], position: number): number {
  let index = -1;
  for (let i = 0; i < chapters.length; i += 1) {
    if (position >= chapters[i].start_seconds) index = i;
  }
  return index;
}

/** Where "previous chapter" should seek to, or null when there is nowhere to go. */
export function previousChapterTarget(chapters: Chapter[], position: number): number | null {
  const current = chapterIndexAt(chapters, position);
  if (current < 0) return null;

  const start = chapters[current].start_seconds;
  if (position - start > RESTART_WINDOW_SECONDS) return start;
  return current > 0 ? chapters[current - 1].start_seconds : start;
}

/** Where "next chapter" should seek to, or null at the last chapter. */
export function nextChapterTarget(chapters: Chapter[], position: number): number | null {
  const next = chapters.find((chapter) => chapter.start_seconds > position);
  return next ? next.start_seconds : null;
}
