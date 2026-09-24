/** Share an episode as a pod.link URL.
 *
 *  pod.link resolves to a page that plays the episode in the browser and offers a
 *  one-tap handoff into Apple Podcasts, Spotify, Overcast, Pocket Casts and the rest, so
 *  the recipient lands on the episode whatever app they use. A Podarium URL would be no
 *  use to them: this server is private, behind auth, and not on the public internet.
 *
 *  Built from the feed URL rather than an Apple id, which keeps Apple out of it and works
 *  for feeds Apple has never listed. Format:
 *      https://pod.link/<base64(feed_url)>/episode/<base64(guid)>
 */

/** btoa only accepts Latin-1, and GUIDs are arbitrary text. */
function base64Url(value: string): string {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

const CREDENTIAL_PARAMS = ["token", "key", "auth", "secret", "pass", "sig", "access", "subscri"];

/** A feed URL that appears to carry a subscriber's credentials: user:pass@ in it, or a
 *  query parameter named like a token. The share link would carry them too, base64 being
 *  no disguise, so such a feed gets no link. */
export function looksPrivate(feedUrl: string): boolean {
  try {
    const url = new URL(feedUrl);
    if (url.username || url.password) return true;
    for (const name of url.searchParams.keys()) {
      const lower = name.toLowerCase();
      if (CREDENTIAL_PARAMS.some((hint) => lower.includes(hint))) return true;
    }
    return false;
  } catch {
    return true;
  }
}

export function podlinkEpisodeUrl(feedUrl: string | null | undefined, guid: string): string | null {
  if (!feedUrl || !guid) return null;
  if (looksPrivate(feedUrl)) return null;
  return `https://pod.link/${base64Url(feedUrl)}/episode/${base64Url(guid)}`;
}
