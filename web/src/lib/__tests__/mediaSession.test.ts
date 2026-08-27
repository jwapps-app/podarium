import { describe, expect, it, vi } from "vitest";

import { mediaSessionHandlers } from "../mediaSession";

function transport() {
  return { resume: vi.fn(), pause: vi.fn(), skip: vi.fn(), seek: vi.fn(), stop: vi.fn() };
}

function handlerFor(table: ReturnType<typeof mediaSessionHandlers>, action: string) {
  return table.find(([name]) => name === action)?.[1];
}

describe("media session actions", () => {
  it("pause pauses, and can never start playback", () => {
    // The bug this exists for: both handlers used to call toggle(), so a "pause" action
    // delivered while already paused started playing. iOS sends these on its own -- an
    // interruption ending, Bluetooth connecting, the app returning to the foreground --
    // so the app appeared to start playing by itself when reopened.
    const t = transport();

    handlerFor(mediaSessionHandlers(t), "pause")!({} as MediaSessionActionDetails);

    expect(t.pause).toHaveBeenCalledOnce();
    expect(t.resume).not.toHaveBeenCalled();
  });

  it("play plays, and can never pause", () => {
    const t = transport();

    handlerFor(mediaSessionHandlers(t), "play")!({} as MediaSessionActionDetails);

    expect(t.resume).toHaveBeenCalledOnce();
    expect(t.pause).not.toHaveBeenCalled();
  });

  it("is idempotent: repeating an action never flips it", () => {
    const t = transport();
    const table = mediaSessionHandlers(t);

    for (let i = 0; i < 3; i += 1) {
      handlerFor(table, "pause")!({} as MediaSessionActionDetails);
    }

    expect(t.pause).toHaveBeenCalledTimes(3);
    expect(t.resume).not.toHaveBeenCalled();
  });

  it("no handler is wired to a toggle", () => {
    // A structural guard: whatever else is added here, calling any single action must not
    // be able to reach both transport directions.
    const table = mediaSessionHandlers(transport());

    for (const [action, handler] of table) {
      if (!handler) continue;
      const t = transport();
      handlerFor(mediaSessionHandlers(t), action)!({ seekTime: 5, seekOffset: 5 } as MediaSessionActionDetails);
      const started = t.resume.mock.calls.length > 0;
      const stopped = t.pause.mock.calls.length > 0;
      expect(started && stopped, `${action} reached both directions`).toBe(false);
    }
  });

  it("skip uses the offset the platform supplies, in the right direction", () => {
    const t = transport();
    const table = mediaSessionHandlers(t);

    handlerFor(table, "seekbackward")!({ seekOffset: 15 } as MediaSessionActionDetails);
    handlerFor(table, "seekforward")!({ seekOffset: 45 } as MediaSessionActionDetails);

    expect(t.skip).toHaveBeenNthCalledWith(1, -15);
    expect(t.skip).toHaveBeenNthCalledWith(2, 45);
  });

  it("track-skip actions stay cleared so the platform shows skip arcs", () => {
    const table = mediaSessionHandlers(transport());

    expect(handlerFor(table, "previoustrack")).toBeNull();
    expect(handlerFor(table, "nexttrack")).toBeNull();
  });
});
