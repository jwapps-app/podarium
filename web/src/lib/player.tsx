import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";

import { api } from "./api";
import { useMediaSession } from "./mediaSession";
import type { Episode } from "./types";

/** How often a playing episode reports its position back to the server. Frequent enough
 *  that a crash loses only seconds, sparse enough that a 40-minute episode is a handful
 *  of writes rather than thousands. */
const POSITION_REPORT_INTERVAL_MS = 15_000;

/** Treat an episode as finished slightly before the true end: encoders and trailing
 *  silence mean `ended` sometimes never fires cleanly. */
const COMPLETION_TAIL_SECONDS = 5;

/** Offered speeds. Fine-grained near 1x, where small changes are actually perceptible,
 *  and coarser above it where they are not. */
export const PLAYBACK_RATES = [0.8, 0.9, 1, 1.1, 1.2, 1.25, 1.5, 1.75, 2, 2.5, 3];

interface PlayerValue {
  episode: Episode | null;
  playing: boolean;
  /** Live position in seconds, updated as it plays. */
  position: number;
  duration: number;
  buffering: boolean;
  error: string | null;
  playbackRate: number;
  /** Whether the full now-playing view is open over the page. */
  expanded: boolean;

  play: (episode: Episode) => void;
  toggle: () => void;
  seek: (seconds: number) => void;
  skip: (delta: number) => void;
  setPlaybackRate: (rate: number) => void;
  setExpanded: (open: boolean) => void;
  stop: () => void;
  /** Registered by the queue so the player can advance when an episode finishes. */
  setAdvanceHandler: (handler: (() => Episode | null) | null) => void;
}

const PlayerContext = createContext<PlayerValue | null>(null);

export function PlayerProvider({ children }: { children: ReactNode }) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const queryClient = useQueryClient();

  const [episode, setEpisode] = useState<Episode | null>(null);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [buffering, setBuffering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [playbackRate, setPlaybackRateState] = useState(1);
  const [expanded, setExpanded] = useState(false);
  // Set from the server default once, and only while the user has not overridden it in
  // this session -- a saved default should not yank the speed out from under someone who
  // has just adjusted it mid-episode.
  const rateTouchedRef = useRef(false);

  // Kept in refs so the reporting effect does not tear down and restart on every tick.
  const episodeRef = useRef<Episode | null>(null);
  const lastReportedRef = useRef(0);
  const advanceRef = useRef<(() => Episode | null) | null>(null);
  const rateRef = useRef(1);

  if (!audioRef.current && typeof Audio !== "undefined") {
    audioRef.current = new Audio();
    audioRef.current.preload = "metadata";
  }

  // Put the element in the document. A detached Audio() plays, but iOS is unreliable about
  // treating one as the page's media session -- background playback and the Now Playing
  // controls both depend on it being a real element in the document.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || audio.isConnected) return;
    audio.setAttribute("aria-hidden", "true");
    audio.style.display = "none";
    document.body.appendChild(audio);
    return () => {
      audio.remove();
    };
  }, []);

  const reportPosition = useCallback(
    (seconds: number, options: { played?: boolean; force?: boolean } = {}) => {
      const current = episodeRef.current;
      if (!current) return;

      const rounded = Math.floor(seconds);
      if (!options.force && !options.played && Math.abs(rounded - lastReportedRef.current) < 5) {
        return;
      }
      lastReportedRef.current = rounded;

      api
        .setState(current.id, {
          position_seconds: rounded,
          ...(options.played === undefined ? {} : { played: options.played }),
        })
        .then(() => {
          queryClient.invalidateQueries({ queryKey: ["episodes"] });
          queryClient.invalidateQueries({ queryKey: ["feeds"] });
        })
        .catch((cause) => console.error("could not save playback position", cause));
    },
    [queryClient],
  );

  const play = useCallback(
    (next: Episode) => {
      const audio = audioRef.current;
      if (!audio) return;

      // Switching episodes: bank the outgoing position before the src changes.
      if (episodeRef.current && episodeRef.current.id !== next.id) {
        reportPosition(audio.currentTime, { force: true });
      }

      setError(null);

      if (episodeRef.current?.id === next.id) {
        void audio.play().catch((cause) => setError(String(cause)));
        return;
      }

      episodeRef.current = next;
      setEpisode(next);
      setPosition(next.position_seconds);
      setDuration(next.duration_seconds ?? 0);
      lastReportedRef.current = next.position_seconds;

      audio.src = next.stream_url;
      audio.load();
      // Assigning src resets playbackRate to 1, so the chosen speed is reapplied here
      // rather than carrying over on its own.
      audio.playbackRate = rateRef.current;

      const startAt = next.played ? 0 : next.position_seconds;
      const beginPlayback = () => {
        // Seeking before metadata arrives is silently ignored, so resume happens here.
        if (startAt > 0 && Number.isFinite(audio.duration) && startAt < audio.duration) {
          audio.currentTime = startAt;
        }
        void audio.play().catch((cause) => setError(String(cause)));
      };

      if (audio.readyState >= 1) {
        beginPlayback();
      } else {
        audio.addEventListener("loadedmetadata", beginPlayback, { once: true });
      }
    },
    [reportPosition],
  );

  const toggle = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || !episodeRef.current) return;
    if (audio.paused) {
      void audio.play().catch((cause) => setError(String(cause)));
    } else {
      audio.pause();
    }
  }, []);

  const seek = useCallback((seconds: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    const bounded = Math.max(0, Math.min(seconds, audio.duration || seconds));
    audio.currentTime = bounded;
    setPosition(bounded);
  }, []);

  const skip = useCallback(
    (delta: number) => {
      const audio = audioRef.current;
      if (!audio) return;
      seek(audio.currentTime + delta);
    },
    [seek],
  );

  const setPlaybackRate = useCallback((rate: number) => {
    const audio = audioRef.current;
    if (audio) audio.playbackRate = rate;
    setPlaybackRateState(rate);
    rateTouchedRef.current = true;
  }, []);

  const { data: appSettings } = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const defaultRate = appSettings?.default_playback_rate;

  useEffect(() => {
    if (defaultRate === undefined || rateTouchedRef.current) return;
    setPlaybackRateState(defaultRate);
    if (audioRef.current) audioRef.current.playbackRate = defaultRate;
  }, [defaultRate]);

  const stop = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      reportPosition(audio.currentTime, { force: true });
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    episodeRef.current = null;
    setEpisode(null);
    setPlaying(false);
    setPosition(0);
    setExpanded(false);
  }, [reportPosition]);

  const setAdvanceHandler = useCallback((handler: (() => Episode | null) | null) => {
    advanceRef.current = handler;
  }, []);

  // Audio element events -> React state.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const onPlay = () => setPlaying(true);
    const onPause = () => {
      setPlaying(false);
      reportPosition(audio.currentTime, { force: true });
    };
    const onTimeUpdate = () => setPosition(audio.currentTime);
    const onDuration = () => {
      if (Number.isFinite(audio.duration)) setDuration(audio.duration);
    };
    const onWaiting = () => setBuffering(true);
    const onPlaying = () => setBuffering(false);
    const onError = () => {
      setBuffering(false);
      setError("Playback failed. The episode may no longer be available from the publisher.");
    };
    const onEnded = () => {
      setPlaying(false);
      reportPosition(audio.duration || 0, { played: true, force: true });
      const next = advanceRef.current?.() ?? null;
      if (next) play(next);
    };

    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onPause);
    audio.addEventListener("timeupdate", onTimeUpdate);
    audio.addEventListener("loadedmetadata", onDuration);
    audio.addEventListener("durationchange", onDuration);
    audio.addEventListener("waiting", onWaiting);
    audio.addEventListener("playing", onPlaying);
    audio.addEventListener("error", onError);
    audio.addEventListener("ended", onEnded);

    return () => {
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onPause);
      audio.removeEventListener("timeupdate", onTimeUpdate);
      audio.removeEventListener("loadedmetadata", onDuration);
      audio.removeEventListener("durationchange", onDuration);
      audio.removeEventListener("waiting", onWaiting);
      audio.removeEventListener("playing", onPlaying);
      audio.removeEventListener("error", onError);
      audio.removeEventListener("ended", onEnded);
    };
  }, [play, reportPosition]);

  // Periodic position reporting while playing.
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      const audio = audioRef.current;
      if (!audio) return;

      // Some encodings never fire `ended`; treat the last few seconds as finished so an
      // episode does not sit at 99% forever.
      if (
        Number.isFinite(audio.duration) &&
        audio.duration > 0 &&
        audio.currentTime >= audio.duration - COMPLETION_TAIL_SECONDS
      ) {
        reportPosition(audio.currentTime, { played: true, force: true });
        return;
      }
      reportPosition(audio.currentTime);
    }, POSITION_REPORT_INTERVAL_MS);

    return () => window.clearInterval(timer);
  }, [playing, reportPosition]);

  // Closing the tab mid-episode should not lose the position.
  useEffect(() => {
    const onUnload = () => {
      const audio = audioRef.current;
      const current = episodeRef.current;
      if (!audio || !current) return;
      // fetch() is cancelled on unload; sendBeacon is the only reliable last write.
      navigator.sendBeacon?.(
        `/api/episodes/${current.id}/state`,
        new Blob([JSON.stringify({ position_seconds: Math.floor(audio.currentTime) })], {
          type: "application/json",
        }),
      );
    };
    window.addEventListener("pagehide", onUnload);
    return () => window.removeEventListener("pagehide", onUnload);
  }, []);

  rateRef.current = playbackRate;

  // The feed is only needed for the show name the lock screen shows as the artist.
  const { data: feeds } = useQuery({ queryKey: ["feeds"], queryFn: api.feeds });
  const showTitle =
    feeds?.find((candidate) => candidate.id === episode?.feed_id)?.title ?? null;

  useMediaSession({
    episode,
    showTitle,
    playing,
    position,
    duration,
    playbackRate,
    toggle,
    skip,
    seek,
    stop,
  });

  const value = useMemo(
    () => ({
      episode,
      playing,
      position,
      duration,
      buffering,
      error,
      playbackRate,
      expanded,
      play,
      toggle,
      seek,
      skip,
      setPlaybackRate,
      setExpanded,
      stop,
      setAdvanceHandler,
    }),
    [
      episode, playing, position, duration, buffering, error, playbackRate, expanded,
      play, toggle, seek, skip, setPlaybackRate, stop, setAdvanceHandler,
    ],
  );

  return <PlayerContext.Provider value={value}>{children}</PlayerContext.Provider>;
}

export function usePlayer(): PlayerValue {
  const value = useContext(PlayerContext);
  if (!value) throw new Error("usePlayer must be used inside PlayerProvider");
  return value;
}
