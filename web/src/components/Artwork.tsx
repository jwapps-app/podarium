import { useState } from "react";

interface Props {
  /** Always an /api/images path from the API -- never a publisher URL. */
  src: string | null;
  alt: string;
  /** Shown when there is no artwork, or when the cache could not fetch it. */
  fallbackText?: string | null;
  className?: string;
}

/** Artwork with a graceful fallback.
 *
 *  A 404 here is routine, not an error: /api/images only serves what the server managed to
 *  cache, and some feeds simply have no image. Rendering initials beats a broken-image icon.
 */
export function Artwork({ src, alt, fallbackText, className }: Props) {
  const [failed, setFailed] = useState(false);

  if (!src || failed) {
    const initials = (fallbackText ?? alt ?? "?")
      .replace(/^(the|a)\s+/i, "")
      .split(/\s+/)
      .slice(0, 2)
      .map((word) => word[0])
      .join("")
      .toUpperCase();
    return (
      <div className={className}>
        <div className="art-fallback" aria-hidden="true">{initials || "?"}</div>
      </div>
    );
  }

  return (
    <div className={className}>
      <img src={src} alt={alt} loading="lazy" onError={() => setFailed(true)} />
    </div>
  );
}
