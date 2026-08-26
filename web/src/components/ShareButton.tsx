import { useEffect, useRef, useState } from "react";

import { CheckIcon, ShareIcon } from "./Icons";

interface Props {
  url: string;
  title: string;
  showTitle?: string | null;
}

/** Share control with a fallback that always leaves you something usable.
 *
 *  Both good paths need a secure context, and Podarium is reached over plain HTTP on a LAN
 *  address: navigator.share and navigator.clipboard are undefined there, and
 *  execCommand("copy") returns false as well. So the last resort is not another silent
 *  copy attempt -- it is showing the link, selected, so it can be copied by hand or
 *  long-pressed into a share sheet. Once this is served over HTTPS the native sheet takes
 *  over and the panel never appears.
 */
export function ShareButton({ url, title, showTitle }: Props) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.select();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const label = showTitle ? `${showTitle} — ${title}` : title;

  const onShare = async () => {
    if (navigator.share) {
      try {
        await navigator.share({ title: label, url });
        return;
      } catch (error) {
        // Dismissing the sheet is not a failure; do not fall through to the panel.
        if ((error as Error)?.name === "AbortError") return;
      }
    }
    setOpen(true);
  };

  const onCopy = async () => {
    let ok = false;
    if (navigator.clipboard) {
      ok = await navigator.clipboard.writeText(url).then(() => true, () => false);
    }
    if (!ok) {
      inputRef.current?.select();
      try {
        ok = document.execCommand("copy");
      } catch {
        ok = false;
      }
    }
    setCopied(ok);
    if (ok) window.setTimeout(() => setCopied(false), 2000);
  };

  return (
    <>
      <button
        className="btn-icon"
        onClick={() => void onShare()}
        aria-label="Share this episode"
        title="Share this episode"
      >
        <ShareIcon />
      </button>

      {open ? (
        <div className="share-backdrop" onClick={() => setOpen(false)}>
          <div
            className="share-panel"
            role="dialog"
            aria-modal="true"
            aria-label="Share this episode"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="share-title">{label}</div>
            <p className="share-hint">
              Opens the episode in whichever podcast app they use — or plays in the browser.
            </p>

            <input ref={inputRef} className="share-url mono" readOnly value={url} />

            <div className="share-actions">
              <button className="btn btn-primary btn-sm" onClick={() => void onCopy()}>
                {copied ? (
                  <>
                    <CheckIcon style={{ width: 14, height: 14 }} /> Copied
                  </>
                ) : (
                  "Copy link"
                )}
              </button>
              <a className="btn btn-sm" href={url} target="_blank" rel="noopener noreferrer">
                Open
              </a>
              <button className="btn btn-sm" onClick={() => setOpen(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
