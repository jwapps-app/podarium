import { describe, expect, it } from "vitest";

import { bundleNameFrom, shouldReload } from "../freshness";

const idle = { playing: false, typing: false, alreadyTriedFor: null };

describe("knowing which build a page is", () => {
  it("reads the bundle from the script's own address", () => {
    expect(bundleNameFrom("https://pod.example/assets/index-DI5siKVx.js")).toBe("index-DI5siKVx.js");
  });

  it("has no build in development", () => {
    expect(bundleNameFrom("http://localhost:5174/src/lib/freshness.ts")).toBeNull();
  });
});

describe("reloading a page the server has outlived", () => {
  it("reloads an idle page running an older build", () => {
    expect(shouldReload({ ...idle, own: "index-old.js", latest: "index-new.js" })).toBe(true);
  });

  it("leaves a current page alone", () => {
    expect(shouldReload({ ...idle, own: "index-new.js", latest: "index-new.js" })).toBe(false);
  });

  it("never interrupts playback or typing", () => {
    const behind = { own: "index-old.js", latest: "index-new.js", alreadyTriedFor: null };
    expect(shouldReload({ ...behind, playing: true, typing: false })).toBe(false);
    expect(shouldReload({ ...behind, playing: false, typing: true })).toBe(false);
  });

  it("tries once per build, not in a loop", () => {
    expect(
      shouldReload({ ...idle, own: "index-old.js", latest: "index-new.js", alreadyTriedFor: "index-new.js" }),
    ).toBe(false);
    // A later build is a new reason.
    expect(
      shouldReload({ ...idle, own: "index-old.js", latest: "index-newer.js", alreadyTriedFor: "index-new.js" }),
    ).toBe(true);
  });

  it("does nothing without both names", () => {
    expect(shouldReload({ ...idle, own: null, latest: "index-new.js" })).toBe(false);
    expect(shouldReload({ ...idle, own: "index-old.js", latest: null })).toBe(false);
  });
});
