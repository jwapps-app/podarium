import { useEffect, useState } from "react";

import { usePlayer } from "../lib/player";

/** What the audio element thinks is going on, on screen, behind ?debug=media.
 *
 *  Playback faults on a phone are close to undiagnosable from a description. "It stops
 *  where it has loaded to" fits a truncated stream, a seek outside the seekable range, a
 *  decode error and a stall, and those want different fixes -- the difference is visible
 *  in readyState, the error code and the buffered ranges, and nowhere else. Safari on iOS
 *  has no console anyone can reach without a Mac and a cable, so the state comes to the
 *  screen instead.
 *
 *  Off unless asked for by query string, so it costs a mounted component that renders
 *  null and nothing else.
 */
export function MediaDebug() {
  const { element } = usePlayer();
  const [, redraw] = useState(0);
  const [log, setLog] = useState<string[]>([]);

  const on = typeof window !== "undefined" && window.location.search.includes("debug=media");

  useEffect(() => {
    if (!on) return;
    const timer = window.setInterval(() => redraw((n) => n + 1), 400);
    return () => window.clearInterval(timer);
  }, [on]);

  // Events rather than polling, because the interesting ones -- a stall, an error, an end
  // that should not have happened -- are over before the next poll.
  useEffect(() => {
    if (!on || !element) return;
    const names = [
      "loadstart", "loadedmetadata", "canplay", "play", "playing", "pause", "waiting",
      "stalled", "suspend", "seeking", "seeked", "ended", "error", "emptied", "abort",
    ];
    const note = (event: Event) => {
      const stamp = new Date().toISOString().slice(14, 22);
      const detail =
        event.type === "error" && element.error ? ` code=${element.error.code}` : "";
      setLog((previous) => [`${stamp} ${event.type}${detail}`, ...previous].slice(0, 10));
    };
    names.forEach((name) => element.addEventListener(name, note));
    return () => names.forEach((name) => element.removeEventListener(name, note));
  }, [on, element]);

  if (!on || !element) return null;

  const ranges = (value: TimeRanges) => {
    const parts: string[] = [];
    for (let i = 0; i < value.length; i += 1) {
      parts.push(`${value.start(i).toFixed(1)}-${value.end(i).toFixed(1)}`);
    }
    return parts.join(",") || "none";
  };

  const controlled =
    "serviceWorker" in navigator && navigator.serviceWorker.controller ? "yes" : "no";

  return (
    <pre
      style={{
        position: "fixed",
        insetInline: 0,
        top: 0,
        zIndex: 9999,
        margin: 0,
        padding: "6px 8px",
        background: "rgba(255,255,255,0.96)",
        borderBottom: "1px solid #999",
        color: "#111",
        font: "11px/1.35 ui-monospace, SFMono-Regular, Menlo, monospace",
        whiteSpace: "pre-wrap",
      }}
    >
      {[
        `sw ${controlled}   rate ${element.playbackRate}`,
        `t ${element.currentTime.toFixed(1)}   dur ${element.duration}`,
        `paused ${element.paused} ended ${element.ended} seeking ${element.seeking}`,
        `ready ${element.readyState} net ${element.networkState} err ${
          element.error ? element.error.code : "-"
        }`,
        `buffered ${ranges(element.buffered)}`,
        `seekable ${ranges(element.seekable)}`,
        ...log,
      ].join("\n")}
    </pre>
  );
}
