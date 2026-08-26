import { useCallback, useRef, useState } from "react";

/** Pointer-based list reordering.
 *
 *  Deliberately not HTML5 drag-and-drop. That API is mouse-only -- `draggable` does
 *  nothing on a touchscreen -- and this queue is meant to be reordered on a phone. Pointer
 *  events cover mouse, touch, and stylus with one code path.
 *
 *  The dragged id lives in a ref, not state: pointermove fires far more often than React
 *  re-renders, and a drop landing in the same tick as the drag starting would otherwise
 *  read a stale null.
 */
export function useDragReorder<T>(
  items: T[],
  getKey: (item: T) => number,
  onReorder: (next: T[]) => void,
  onCommit: (next: T[]) => void,
) {
  const draggingRef = useRef<number | null>(null);
  const [draggingKey, setDraggingKey] = useState<number | null>(null);
  const rowRefs = useRef(new Map<number, HTMLElement>());
  const latestRef = useRef(items);
  latestRef.current = items;

  const registerRow = useCallback((key: number, element: HTMLElement | null) => {
    if (element) rowRefs.current.set(key, element);
    else rowRefs.current.delete(key);
  }, []);

  const onPointerDown = useCallback(
    (key: number) => (event: React.PointerEvent) => {
      // Ignore right-clicks and anything that is not a primary press.
      if (event.button !== 0) return;
      event.preventDefault();
      draggingRef.current = key;
      setDraggingKey(key);
      (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
    },
    [],
  );

  const onPointerMove = useCallback(
    (event: React.PointerEvent) => {
      const dragging = draggingRef.current;
      if (dragging === null) return;

      const current = latestRef.current;
      const fromIndex = current.findIndex((item) => getKey(item) === dragging);
      if (fromIndex === -1) return;

      // Which row is the pointer over? Compare against each row's midpoint so the swap
      // happens when the cursor passes the halfway mark rather than at the very edge.
      let toIndex = fromIndex;
      for (let index = 0; index < current.length; index += 1) {
        const element = rowRefs.current.get(getKey(current[index]));
        if (!element) continue;
        const rect = element.getBoundingClientRect();
        const midpoint = rect.top + rect.height / 2;
        if (index < fromIndex && event.clientY < midpoint) {
          toIndex = index;
          break;
        }
        if (index > fromIndex && event.clientY > midpoint) {
          toIndex = index;
        }
      }

      if (toIndex === fromIndex) return;

      const next = [...current];
      const [moved] = next.splice(fromIndex, 1);
      next.splice(toIndex, 0, moved);
      onReorder(next);
    },
    [getKey, onReorder],
  );

  const endDrag = useCallback(() => {
    if (draggingRef.current === null) return;
    draggingRef.current = null;
    setDraggingKey(null);
    onCommit(latestRef.current);
  }, [onCommit]);

  return {
    draggingKey,
    registerRow,
    /** Spread onto the drag handle. */
    handleProps: (key: number) => ({
      onPointerDown: onPointerDown(key),
      onPointerMove,
      onPointerUp: endDrag,
      onPointerCancel: endDrag,
    }),
  };
}
