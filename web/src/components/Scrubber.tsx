import { useEffect, useRef, useState } from "react";

interface Props {
  position: number;
  duration: number;
  onSeek: (seconds: number) => void;
  className?: string;
}

/** How long to wait for a release that may never come. Generous: it is a fallback for a
 *  lost event, not part of normal dragging, and firing early is the failure it prevents. */
const BACKSTOP_MS = 1200;

/** The seek bar, which commits one seek when you let go rather than one per movement.
 *
 *  React reports a range input's `onChange` on every movement, so seeking from the handler
 *  turned one drag into dozens of seeks. Past the buffered region each is a fresh byte-range
 *  request, and a burst of them was measured leaving the element stalled with an empty
 *  buffer. So the thumb follows your finger locally and the seek happens on release.
 *
 *  The timeout is a backstop for a release that never arrives -- a drag that ends off the
 *  element, or a platform that reports pointer events differently. It must not fire while a
 *  finger is still down: a real drag across a phone takes a couple of seconds and pauses on
 *  the way, so a plain timer went off mid-gesture and committed a seek, then another, then
 *  another -- the behaviour this component exists to prevent, reintroduced by its own safety
 *  net. It re-arms instead of committing while the pointer is held.
 */
export function Scrubber({ position, duration, onSeek, className }: Props) {
  const [dragging, setDragging] = useState<number | null>(null);
  const timeout = useRef<number | null>(null);
  const held = useRef(false);
  // The same value as `dragging`, kept where committing can read it without reaching into
  // a state updater. Seeking from inside one made the seek a side effect of rendering,
  // which StrictMode is entitled to run twice -- one drag, two seeks.
  const pending = useRef<number | null>(null);

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
    const value = pending.current;
    pending.current = null;
    setDragging(null);
    if (value !== null) onSeek(value);
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
        pending.current = Number(event.target.value);
        setDragging(pending.current);
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
