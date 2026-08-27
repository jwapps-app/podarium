import { useEffect, useState } from "react";

/** Settle on a value only after it stops changing.
 *
 *  Search runs a LIKE across every episode, so firing one per keystroke would put a dozen
 *  scans behind a five-letter word and race their answers into the list.
 */
export function useDebounced<T>(value: T, delayMs: number): T {
  const [settled, setSettled] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);

  return settled;
}
