import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { forgetEpisode, listSaved, offlineSupported, saveEpisode } from "./offline";

interface OfflineContextValue {
  /** Whether saving to the device is possible at all in this browser and context. */
  supported: boolean;
  saved: Set<number>;
  /** Episodes with a save in flight, so a row can show progress. */
  pending: Set<number>;
  save: (id: number) => Promise<void>;
  forget: (id: number) => Promise<void>;
  error: string | null;
}

const OfflineContext = createContext<OfflineContextValue | null>(null);

/** One place that knows what is on the device.
 *
 *  Centralised because the alternative is every episode row asking the service worker the
 *  same question, and a hundred-row list would then hold a hundred message round-trips
 *  open at once.
 */
export function OfflineProvider({ children }: { children: React.ReactNode }) {
  const [saved, setSaved] = useState<Set<number>>(new Set());
  const [pending, setPending] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const supported = offlineSupported();

  const refresh = useCallback(async () => {
    if (!supported) return;
    try {
      setSaved(new Set(await listSaved()));
    } catch {
      // A worker that is not controlling the page yet simply has nothing saved to report.
      setSaved(new Set());
    }
  }, [supported]);

  useEffect(() => {
    void refresh();
    // The worker takes control a moment after a first load, and only then can it answer.
    if (!supported) return;
    const onControllerChange = () => void refresh();
    navigator.serviceWorker.addEventListener("controllerchange", onControllerChange);
    return () =>
      navigator.serviceWorker.removeEventListener("controllerchange", onControllerChange);
  }, [refresh, supported]);

  const mark = (id: number, inFlight: boolean) =>
    setPending((current) => {
      const next = new Set(current);
      if (inFlight) next.add(id);
      else next.delete(id);
      return next;
    });

  const save = useCallback(async (id: number) => {
    setError(null);
    mark(id, true);
    try {
      await saveEpisode(id);
      setSaved((current) => new Set(current).add(id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      mark(id, false);
    }
  }, []);

  const forget = useCallback(async (id: number) => {
    setError(null);
    mark(id, true);
    try {
      await forgetEpisode(id);
      setSaved((current) => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      mark(id, false);
    }
  }, []);

  const value = useMemo(
    () => ({ supported, saved, pending, save, forget, error }),
    [supported, saved, pending, save, forget, error],
  );

  return <OfflineContext.Provider value={value}>{children}</OfflineContext.Provider>;
}

export function useOffline(): OfflineContextValue {
  const value = useContext(OfflineContext);
  if (!value) throw new Error("useOffline must be used inside OfflineProvider");
  return value;
}
