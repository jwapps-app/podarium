import { useEffect, useRef, useState } from "react";

interface Props {
  position: number;
  duration: number;
  onSeek: (seconds: number) => void;
  className?: string;
}

/** The seek bar, which commits once rather than continuously.
 *
 *  React reports a range input's `onChange` on every movement, so seeking from the handler
 *  meant a drag across the bar issued dozens of seeks. Inside the buffered region that is
 *  merely wasteful; past it, every one of them is a fresh byte-range request to the
 *  publisher. Desktop browsers coalesce that and it goes unnoticed; iOS does not, and
 *  playback stops.
 *
 *  So the thumb follows your finger locally and the seek happens when you let go.
 *
 *  The timeout is a backstop for a release that never arrives -- a drag that ends off the
 *  element, or a platform that reports pointer events differently. It must not fire while
 *  a finger is still down: a real drag across a phone screen takes a couple of seconds and
 *  pauses on the way, so a plain timer went off mid-drag and committed a seek, then another,
 *  then another. That is the behaviour this component exists to prevent, reintroduced by its
 *  own safety net. So it re-arms instead of committing while the pointer is held.
 */
/** How long to wait for a release that may never come. Generous: it is a fallback for a
 *  lost event, not part of normal dragging, and firing early is the failure it prevents. */
const BACKSTOP_MS = 1200;

export function Scrubber({ position, duration, onSeek, className }: Props) {
  const [dragging, setDragging] = useState<number | null>(null);
  const timeout = useRef<number | null>(null);
  const held = useRef(false);

  useEffect(() => () => {
    if (timeout.current !== null) window.clearTimeout(timeout.current);
  }, []);

  const shown = dragging ?? position;
  const max = Math.max(duration, 1);
  const percent = duration > 0 ? (shown / duration) * 100 : 0;

  const clearBackstop = () => {
    if (timeout.current !== null) {
      window.clearTimeout(timeout.current);
      timeout.current = null;
    }
  };

  const commit = () => {
    held.current = false;
    clearBackstop();
    setDragging((value) => {
      if (value !== null) onSeek(value);
      return null;
    });
  };

  /** Re-arm while the finger is down; only commit once it is not. */
  const armBackstop = () => {
    clearBackstop();
    timeout.current = window.setTimeout(() => {
      if (held.current) armBackstop();
      else commit();
    }, BACKSTOP_MS);
  };

  return (
    <input
      className={className ?? "scrubber"}
      type="range"
      min={0}
      max={max}
      step={1}
      value={Math.min(shown, max)}
      style={{ ["--progress" as string]: `${percent}%` }}
      onChange={(event) => {
        setDragging(Number(event.target.value));
        armBackstop();
      }}
      onPointerDown={() => {
        held.current = true;
      }}
      onPointerUp={commit}
      onPointerCancel={commit}
      onKeyUp={commit}
      onBlur={commit}
      aria-label="Seek"
    />
  );
}
