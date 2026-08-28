import { fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Scrubber } from "../Scrubber";

/** The seek bar's contract is about *when* it seeks, not what it looks like.
 *
 *  Note what this file cannot do: jsdom computes no layout, so the bug that actually broke
 *  scrubbing on a phone -- the control having no width in one of the two places it is used
 *  -- is invisible here. getBoundingClientRect returns zero for everything. Catching that
 *  needs a real browser. What is testable is the timing, which is where the other real bug
 *  lived: a backstop timer that fired in the middle of a slow drag and turned one gesture
 *  into a burst of seeks.
 */
describe("Scrubber", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const drag = (input: HTMLElement, value: number) =>
    fireEvent.change(input, { target: { value: String(value) } });

  function setup() {
    const onSeek = vi.fn();
    const { getByLabelText } = render(
      <Scrubber position={0} duration={1000} onSeek={onSeek} />,
    );
    return { onSeek, input: getByLabelText("Seek") };
  }

  it("does not seek while the finger is still moving", () => {
    const { onSeek, input } = setup();
    fireEvent.pointerDown(input);
    drag(input, 100);
    drag(input, 200);
    drag(input, 300);

    expect(onSeek).not.toHaveBeenCalled();
  });

  it("seeks once, to where you let go", () => {
    const { onSeek, input } = setup();
    fireEvent.pointerDown(input);
    drag(input, 100);
    drag(input, 400);
    fireEvent.pointerUp(input);

    expect(onSeek).toHaveBeenCalledTimes(1);
    expect(onSeek).toHaveBeenCalledWith(400);
  });

  it("does not fire the backstop mid-drag, however long the drag takes", () => {
    // The bug this pins down. A real drag across a phone screen takes seconds and pauses
    // on the way; a plain timer went off during the gesture and committed a seek, then
    // another, then another -- six in three seconds, measured on a device.
    const { onSeek, input } = setup();
    fireEvent.pointerDown(input);

    for (const value of [100, 200, 300, 400, 500]) {
      drag(input, value);
      vi.advanceTimersByTime(2000); // far longer than the backstop
    }
    expect(onSeek).not.toHaveBeenCalled();

    fireEvent.pointerUp(input);
    expect(onSeek).toHaveBeenCalledTimes(1);
    expect(onSeek).toHaveBeenCalledWith(500);
  });

  it("still commits when no release ever arrives", () => {
    // A drag that ends off the element, or a platform that reports pointers differently.
    // The backstop exists for this and must not be lost to the guard above.
    const { onSeek, input } = setup();
    drag(input, 250);
    vi.advanceTimersByTime(2000);

    expect(onSeek).toHaveBeenCalledExactlyOnceWith(250);
  });

  it("commits a cancelled gesture rather than dropping it", () => {
    const { onSeek, input } = setup();
    fireEvent.pointerDown(input);
    drag(input, 700);
    fireEvent.pointerCancel(input);

    expect(onSeek).toHaveBeenCalledExactlyOnceWith(700);
  });

  it("commits when the keyboard moves it and the key comes up", () => {
    const { onSeek, input } = setup();
    drag(input, 60);
    fireEvent.keyUp(input, { key: "ArrowRight" });

    expect(onSeek).toHaveBeenCalledExactlyOnceWith(60);
  });

  it("does not seek at all when nothing moved", () => {
    const { onSeek, input } = setup();
    fireEvent.pointerDown(input);
    fireEvent.pointerUp(input);
    vi.advanceTimersByTime(5000);

    expect(onSeek).not.toHaveBeenCalled();
  });
});
