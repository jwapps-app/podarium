import { describe, expect, it } from "vitest";

import { decideHandover, finalWriteNeeded, RESUME_GRACE_MS, type ServerPosition } from "../handover";

const at = (seconds: number, played = false): ServerPosition => ({
  position_seconds: seconds,
  played,
  audio_version: "p",
  changed_at: "2026-11-09T21:58:25Z",
});

describe("picking an episode up from another device", () => {
  it("moves a paused player to where the other device got to", () => {
    // Paused on the desktop at 10:00, carried on in the car to 55:00.
    expect(decideHandover({ position: 600, paused: true, resumedAgoMs: null }, at(3300))).toEqual({
      kind: "seek",
      to: 3300,
    });
  });

  it("moves one that was resumed a moment ago, since the answer arrives after play", () => {
    expect(decideHandover({ position: 602, paused: false, resumedAgoMs: 1500 }, at(3300))).toEqual({
      kind: "seek",
      to: 3300,
    });
  });

  it("leaves a player that is well into playing alone", () => {
    const settled = { position: 900, paused: false, resumedAgoMs: RESUME_GRACE_MS + 1 };
    expect(decideHandover(settled, at(3300))).toEqual({ kind: "keep" });
    expect(decideHandover(settled, at(3300, true))).toEqual({ kind: "keep" });
  });

  it("does nothing when the two already agree", () => {
    expect(decideHandover({ position: 600, paused: true, resumedAgoMs: null }, at(597))).toEqual({
      kind: "keep",
    });
  });

  it("follows a rewind as readily as progress", () => {
    expect(decideHandover({ position: 3300, paused: true, resumedAgoMs: null }, at(600))).toEqual({
      kind: "seek",
      to: 600,
    });
  });

  it("does not undo an intro skip on an episode nobody has started", () => {
    expect(decideHandover({ position: 45, paused: true, resumedAgoMs: null }, at(0))).toEqual({
      kind: "keep",
    });
  });

  it("lets go of an episode finished elsewhere", () => {
    expect(decideHandover({ position: 600, paused: true, resumedAgoMs: null }, at(3600, true))).toEqual({
      kind: "unload",
    });
  });
});

describe("the write a closing page sends", () => {
  it("is not sent by a page that has only been sitting paused", () => {
    // The case that lost an hour: paused and reported at 600, reloaded much later.
    expect(finalWriteNeeded(600.4, 600, 0)).toBe(false);
  });

  it("is sent when the playhead has moved since the last report", () => {
    expect(finalWriteNeeded(603.2, 600, 0)).toBe(true);
  });

  it("is sent when listening time has not gone up yet", () => {
    expect(finalWriteNeeded(600.4, 600, 3.7)).toBe(true);
  });
});
