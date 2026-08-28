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
 *  So the thumb follows your finger locally and the seek happens when you let go. The
 *  timeout is a backstop for the cases where no release event arrives -- a drag that ends
 *  off the element, or a platform that reports pointer events differently.
 */
export function Scrubber({ position, duration, onSeek, className }: Props) {
  const [dragging, setDragging] = useState<number | null>(null);
  const timeout = useRef<number | null>(null);

  useEffect(() => () => {
    if (timeout.current !== null) window.clearTimeout(timeout.current);
  }, []);

  const shown = dragging ?? position;
  const max = Math.max(duration, 1);
  const percent = duration > 0 ? (shown / duration) * 100 : 0;

  const commit = () => {
    if (timeout.current !== null) {
      window.clearTimeout(timeout.current);
      timeout.current = null;
    }
    setDragging((value) => {
      if (value !== null) onSeek(value);
      return null;
    });
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
        if (timeout.current !== null) window.clearTimeout(timeout.current);
        timeout.current = window.setTimeout(commit, 400);
      }}
      onPointerUp={commit}
      onPointerCancel={commit}
      onKeyUp={commit}
      onBlur={commit}
      aria-label="Seek"
    />
  );
}
