import { describe, expect, it } from "vitest";

import { podlinkEpisodeUrl } from "../share";

/** A Podarium URL is no use to a recipient -- this server is private, behind auth, and off
 *  the public internet. pod.link resolves to a page that plays the episode and hands off
 *  into whatever podcast app the recipient actually uses. */
describe("podlinkEpisodeUrl", () => {
  const feed = "https://feeds.megaphone.fm/GLT1412515089";
  const guid = "fc78372e-a00f-11f1-b4a6-67ef23d80453";

  it("builds the format pod.link expects", () => {
    // Verified against the live service for this exact episode.
    expect(podlinkEpisodeUrl(feed, guid)).toBe(
      "https://pod.link/aHR0cHM6Ly9mZWVkcy5tZWdhcGhvbmUuZm0vR0xUMTQxMjUxNTA4OQ/episode/ZmM3ODM3MmUtYTAwZi0xMWYxLWI0YTYtNjdlZjIzZDgwNDUz",
    );
  });

  it("uses URL-safe base64 with no padding", () => {
    const url = podlinkEpisodeUrl("https://a.example/f?x=1&y=2", "guid/with+chars==")!;
    // Test the encoded segments, not the whole URL -- pod.link's own path has slashes.
    const [encodedFeed, , encodedGuid] = url.replace("https://pod.link/", "").split("/");
    expect(encodedFeed).not.toMatch(/[+/=]/);
    expect(encodedGuid).not.toMatch(/[+/=]/);
  });

  it("handles non-ASCII, which btoa alone cannot", () => {
    // Some feeds use titles or paths with accented characters in the GUID.
    expect(() => podlinkEpisodeUrl("https://a.example/didaché", "épisode-ü")).not.toThrow();
    expect(podlinkEpisodeUrl("https://a.example/didaché", "épisode-ü")).toContain("/episode/");
  });

  it("returns null when either half is missing", () => {
    expect(podlinkEpisodeUrl(null, guid)).toBeNull();
    expect(podlinkEpisodeUrl(undefined, guid)).toBeNull();
    expect(podlinkEpisodeUrl(feed, "")).toBeNull();
  });

  it("round-trips back to the original feed and guid", () => {
    const url = podlinkEpisodeUrl(feed, guid)!;
    const [encodedFeed, , encodedGuid] = url.replace("https://pod.link/", "").split("/");
    const decode = (v: string) =>
      new TextDecoder().decode(
        Uint8Array.from(
          atob(v.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (v.length % 4)) % 4)),
          (c) => c.charCodeAt(0),
        ),
      );
    expect(decode(encodedFeed)).toBe(feed);
    expect(decode(encodedGuid)).toBe(guid);
  });
});
