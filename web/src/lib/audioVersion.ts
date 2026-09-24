/** Which copy of an episode a stream URL names.
 *
 *  Trimming makes a second copy with its own clock, and the server stores every second
 *  on the original's. A position or bookmark therefore says which copy it was heard on,
 *  read from the URL the player is actually using -- not from the episode record, which
 *  may have moved on to the trimmed copy while the player kept the original.
 */
export function audioVersion(streamUrl: string | null | undefined): "o" | "p" | undefined {
  if (!streamUrl) return undefined;
  const match = /[?&]v=([op])(?:&|$)/.exec(streamUrl);
  return match ? (match[1] as "o" | "p") : undefined;
}
