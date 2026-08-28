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
import { sponsorSkipTarget } from "./chapters";
import { shouldHonorPlatformResume, useMediaSession } from "./mediaSession";
import type { Chapter, Episode } from "./types";

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

/** The most audio one timeupdate tick can plausibly represent.
 *
 *  timeupdate fires about four times a second, so a tick covers a quarter-second of audio
 *  at 1x and about a second at the fastest offered speed. Anything larger is a seek, not
 *  listening. Generous enough to survive a stalled tab catching up, tight enough that a
 *  ten-second skip never counts. */
const MAX_TICK_SECONDS = 5;

/** How far before the true end to hand over to the next episode.
 *
 *  Long enough that the switch happens while audio is unmistakably still playing, short
 *  enough to cost nothing audible -- the last second of a podcast is silence or a fading
 *  outro. Waiting for `ended` instead means the handover has to happen after audio has
 *  stopped, which is exactly when iOS suspends a backgrounded page. */
const HANDOVER_LEAD_SECONDS = 1;

/** How close the element's duration must be to the feed's before it is believed.
 *
 *  A stream that is still arriving can report the duration of what it has, not of what it
 *  is -- which would put "the end" somewhere in the middle. Feed durations are themselves
 *  approximate, so this is a loose sanity check rather than a comparison: it is looking for
 *  a duration that is obviously short, not one that is a few seconds out. */
const DURATION_AGREEMENT = 0.95;

interface PlayerValue {
  episode: Episode | null;
  playing: boolean;
  buffering: boolean;
  error: string | null;
  playbackRate: number;
  /** Whether the full now-playing view is open over the page. */
  expanded: boolean;

  play: (episode: Episode) => void;
  toggle: () => void;
  /** Explicit transport, for callers where "the other one" is not what was asked for. */
  resume: () => void;
  pause: () => void;
  seek: (seconds: number) => void;
  skip: (delta: number) => void;
  setPlaybackRate: (rate: number) => void;
  setExpanded: (open: boolean) => void;
  stop: () => void;
  /** Registered by the queue so the player can advance when an episode finishes. */
  setAdvanceHandler: (handler: (() => Episode | null) | null) => void;
  /** Minutes remaining on the sleep timer, or null when none is running. */
  sleepMinutes: number | null;
  /** Whether playback stops at the end of the current episode. */
  sleepAtEnd: boolean;
  /** Minutes, or "episode" to stop at the end of what is playing, or null to cancel. */
  setSleepTimer: (minutes: number | "episode" | null) => void;
}

const PlayerContext = createContext<PlayerValue | null>(null);

/** The one part of the player that changes every second.

    Its own context, because the main one is consumed by every episode row on screen: with
    position in there, each tick of playback re-rendered a hundred rows that display
    nothing time-related. Only the two surfaces that actually draw a clock -- the bar and
    the full view -- subscribe to this one. */
interface PlayerProgressValue {
  /** Live position in seconds, ticking once a second while playing. */
  position: number;
  duration: number;
}

const PlayerProgressContext = createContext<PlayerProgressValue | null>(null);

/** Hoisted so the query key is a stable reference across renders. */
const RESUME_FILTERS = { in_progress: true, limit: 1 } as const;

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

  // Sleep timer. Held as a deadline rather than a countdown so that a tab suspended in the
  // background -- which a phone does constantly -- wakes up with the correct time left
  // instead of however far the interval happened to get.
  const [sleepUntil, setSleepUntil] = useState<number | null>(null);
  const [sleepMinutes, setSleepMinutes] = useState<number | null>(null);
  // Mirrored as state for display and held in a ref for the ended handler, which must
  // read the current value without the effect being torn down and rebuilt each time.
  const [sleepAtEnd, setSleepAtEnd] = useState(false);

  // Whether audio has actually played since this page loaded.
  //
  // Restoring an episode on launch deliberately does not count. Until you press play,
  // Podarium is holding an episode, not playing one -- and an app that is not the device's
  // current audio app should not be registered with the platform as if it were. That
  // registration is what a queued "play" from a finished phone call lands on when the page
  // resumes, which is how opening the app could start playback by itself.
  const [sessionActive, setSessionActive] = useState(false);
  const sleepAtEndRef = useRef(false);

  // Kept in refs so the reporting effect does not tear down and restart on every tick.
  const episodeRef = useRef<Episode | null>(null);
  const lastReportedRef = useRef(0);
  // Audio that has actually played since the last report, and the playhead at the previous
  // tick. See the timeupdate handler for why this is measured rather than derived.
  const listenedRef = useRef(0);
  const lastTickRef = useRef<number | null>(null);
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

      // Whole seconds, and only what has not been sent. Kept out of the payload entirely
      // when it is zero so a state write that involves no listening -- marking played,
      // starring -- cannot be mistaken for any.
      const listened = Math.floor(listenedRef.current);
      if (listened > 0) listenedRef.current -= listened;

      api
        .setState(current.id, {
          position_seconds: rounded,
          ...(listened > 0 ? { listened_delta: listened } : {}),
          ...(options.played === undefined ? {} : { played: options.played }),
        })
        .then(() => {
          // Only a state *transition* is worth refetching lists over. The routine
          // five-second position report used to invalidate too, which made an open inbox
          // refetch its full payload every five seconds for the whole length of an
          // episode -- megabytes an hour on a phone, for a progress bar nobody was
          // watching. Rows catch up on the next natural refetch; a pause, a played mark,
          // or switching episodes (all force or played) still refresh immediately.
          if (options.played === undefined && !options.force) return;
          queryClient.invalidateQueries({ queryKey: ["episodes"] });
          queryClient.invalidateQueries({ queryKey: ["feeds"] });
        })
        .catch((cause) => console.error("could not save playback position", cause));
    },
    [queryClient],
  );

  /** Load an episode into the player, optionally starting it.
   *
   *  Without autoplay this is how the app comes back up holding whatever you were last
   *  listening to: the bar appears, seeked to your position, waiting on a tap. It has to
   *  be a separate path rather than play()-then-pause() because a browser will refuse the
   *  play() outright without a user gesture behind it, and a refusal surfaces as a
   *  playback error the user never caused.
   */
  const cue = useCallback(
    (next: Episode, { autoplay }: { autoplay: boolean }) => {
      const audio = audioRef.current;
      if (!audio) return;

      const previous = episodeRef.current;

      // Switching episodes: bank the outgoing position before the src changes.
      if (previous && previous.id !== next.id) {
        reportPosition(audio.currentTime, { force: true });
      }

      setError(null);

      if (episodeRef.current?.id === next.id) {
        if (autoplay) void audio.play().catch((cause) => setError(String(cause)));
        return;
      }

      episodeRef.current = next;
      outroFiredRef.current = false;
      lastTickRef.current = null;
      setEpisode(next);
      setPosition(next.position_seconds);
      setDuration(next.duration_seconds ?? 0);
      lastReportedRef.current = next.position_seconds;

      // Moving to a different show adopts that show's speed. Moving within one show does
      // not: the queue advancing to the next episode of the same podcast should not undo a
      // speed you just chose, but arriving at a different podcast should not inherit it.
      if (previous?.feed_id !== next.feed_id) {
        const showRate = feedRatesRef.current.get(next.feed_id);
        if (showRate) {
          rateRef.current = showRate;
          setPlaybackRateState(showRate);
          // A show's own setting outranks the global default, so stop that effect
          // reapplying the global over the top when settings resolve.
          rateTouchedRef.current = true;
        }
      }

      audio.src = next.stream_url;
      audio.load();
      // Assigning src resets playbackRate to 1, so the chosen speed is reapplied here
      // rather than carrying over on its own.
      audio.playbackRate = rateRef.current;

      // Only when starting from the beginning. Resuming at 4:12 means you were already
      // past the intro, and jumping forward from there would lose your place.
      const intro = feedSkipsRef.current.get(next.feed_id)?.intro ?? 0;
      const resumeAt = next.played ? 0 : next.position_seconds;
      const startAt = resumeAt > 0 ? resumeAt : intro;
      const begin = () => {
        // Seeking before metadata arrives is silently ignored, so resume happens here.
        if (startAt > 0 && Number.isFinite(audio.duration) && startAt < audio.duration) {
          audio.currentTime = startAt;
        }
        if (autoplay) void audio.play().catch((cause) => setError(String(cause)));
      };

      if (audio.readyState >= 1) {
        begin();
      } else {
        audio.addEventListener("loadedmetadata", begin, { once: true });
      }
    },
    [reportPosition],
  );

  const play = useCallback((next: Episode) => cue(next, { autoplay: true }), [cue]);

  /** Start playing. Idempotent -- already playing is not an error. */
  const resume = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || !episodeRef.current) return;
    if (audio.paused) {
      void audio.play().catch((cause) => setError(String(cause)));
    }
  }, []);

  /** Stop playing, without unloading the episode. Idempotent. */
  const pause = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || !episodeRef.current) return;
    if (!audio.paused) audio.pause();
  }, []);

  /** For the app's own play/pause button, where one control means both. Deliberately not
   *  what the OS media controls call -- see mediaSession.ts. */
  const toggle = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || !episodeRef.current) return;
    if (audio.paused) resume();
    else pause();
  }, [resume, pause]);

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

  // Per-show speed, resolved when an episode is cued. Held in a ref so cue() can read the
  // current map without being rebuilt -- and therefore re-cueing -- every time feeds refetch.
  // Populated from the feeds query further down, which the lock screen already needs.
  const feedRatesRef = useRef(new Map<number, number>());
  // Per-show intro/outro trim, read at cue time and at the end of an episode.
  const feedSkipsRef = useRef(new Map<number, { intro: number; outro: number }>());
  // Reset per episode; see the outro check in the timeupdate handler.
  const outroFiredRef = useRef(false);
  // Chapters and whether this show wants ad breaks skipped, for the timeupdate check.
  const skipSponsorsRef = useRef(false);
  const chaptersRef = useRef<Chapter[]>([]);

  // Come back up holding whatever was playing when the app was last closed.
  //
  // Position has always been saved server-side, but nothing remembered *which* episode, so
  // reopening the PWA left an empty player and no route back to the thing you were halfway
  // through -- on a phone, where the app is evicted from memory constantly, that is most
  // times you open it. The server's resume list already answers "what was I listening to",
  // so the first entry is loaded paused, at its saved position.
  const { data: resumable } = useQuery({
    queryKey: ["episodes", RESUME_FILTERS],
    queryFn: () => api.episodes(RESUME_FILTERS),
  });

  const restoredRef = useRef(false);

  useEffect(() => {
    // Exactly once per launch, on the query's first answer.
    //
    // The flag is set before the checks below rather than after, because this query is
    // invalidated every time playback reports a position. Marking it only on a successful
    // cue would leave the effect live for the rest of the session, and a later refetch
    // would then reload the player seconds after the user had deliberately stopped it.
    if (restoredRef.current || resumable === undefined) return;
    restoredRef.current = true;

    const candidate = resumable.items[0];
    if (!candidate || episodeRef.current) return;
    cue(candidate, { autoplay: false });
  }, [resumable, cue]);

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
    setSessionActive(false);
  }, [reportPosition]);

  const setSleepTimer = useCallback((minutes: number | "episode" | null) => {
    if (minutes === null) {
      sleepAtEndRef.current = false;
      setSleepAtEnd(false);
      setSleepUntil(null);
      setSleepMinutes(null);
      return;
    }
    if (minutes === "episode") {
      // Handled by the ended handler rather than a clock: "finish this episode" has no
      // duration until you know how much is left, and the answer changes if you seek.
      sleepAtEndRef.current = true;
      setSleepAtEnd(true);
      setSleepUntil(null);
      setSleepMinutes(null);
      return;
    }
    sleepAtEndRef.current = false;
    setSleepAtEnd(false);
    setSleepUntil(Date.now() + minutes * 60_000);
    setSleepMinutes(minutes);
  }, []);

  // Pause when the deadline passes. Ticks once a second, which is enough for a display
  // rounded to minutes and cheap enough not to care about.
  useEffect(() => {
    if (sleepUntil === null) return;
    const tick = window.setInterval(() => {
      const remaining = sleepUntil - Date.now();
      if (remaining <= 0) {
        audioRef.current?.pause();
        sleepAtEndRef.current = false;
        setSleepAtEnd(false);
        setSleepUntil(null);
        setSleepMinutes(null);
        return;
      }
      setSleepMinutes(Math.ceil(remaining / 60_000));
    }, 1000);
    return () => window.clearInterval(tick);
  }, [sleepUntil]);

  const setAdvanceHandler = useCallback((handler: (() => Episode | null) | null) => {
    advanceRef.current = handler;
  }, []);

  // Audio element events -> React state.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const onPlay = () => {
      setPlaying(true);
      setSessionActive(true);
    };
    const onPause = () => {
      setPlaying(false);
      // Dropped so the gap while paused is not counted when playback resumes.
      lastTickRef.current = null;
      reportPosition(audio.currentTime, { force: true });
    };
    // Whole seconds only. timeupdate fires around four times a second, and every one
    // was a React state change re-rendering every consumer of the player context; the
    // display is in whole seconds anyway, so the extra ticks bought nothing.
    const onTimeUpdate = () => {
      // How much audio actually played, measured between ticks.
      //
      // Not derived from the played flag, which is how an inbox gets cleared and says the
      // opposite of having listened. Not derived from position either: skipping to the end
      // of a three-hour episode would credit three hours. Only the playhead moving forward
      // by about the amount a tick can cover counts -- a seek, in either direction,
      // produces a jump far outside that and is ignored, which is exactly right, because
      // nobody listened to the part that was skipped.
      const previous = lastTickRef.current;
      lastTickRef.current = audio.currentTime;
      if (previous !== null && !audio.paused) {
        const advanced = audio.currentTime - previous;
        if (advanced > 0 && advanced <= MAX_TICK_SECONDS) {
          listenedRef.current += advanced;
        }
      }

      setPosition((current) =>
        Math.floor(current) === Math.floor(audio.currentTime) ? current : audio.currentTime,
      );

      // Ad breaks the publisher marked. Seeking inside the handler is safe: the jump
      // lands outside the break, so the next tick finds nothing to skip.
      if (skipSponsorsRef.current && chaptersRef.current.length > 0 && !audio.paused) {
        const past = sponsorSkipTarget(chaptersRef.current, audio.currentTime);
        if (past !== null && past > audio.currentTime) {
          audio.currentTime = past;
          return;
        }
      }

      // Hand over before the file actually runs out.
      //
      // Waiting for `ended` means waiting for audio to stop, and on iOS a backgrounded page
      // is suspended as soon as it does -- the queue then advances whenever the app is next
      // opened, which is not what "play the next one" means. Crossing over a beat early
      // keeps the handover inside a session that is still playing.
      //
      // The same path serves a show with a standing sign-off, whose outro is simply a
      // larger margin. Guarded by a flag rather than by position, because timeupdate keeps
      // firing inside that window and would otherwise re-report and re-advance several
      // times a second.
      const outro = episodeRef.current
        ? feedSkipsRef.current.get(episodeRef.current.feed_id)?.outro ?? 0
        : 0;
      const margin = Math.max(outro, HANDOVER_LEAD_SECONDS);

      // Only when the playhead *arrived* here by playing.
      //
      // `advanced` is the same natural-progression test the listening counter uses: a tick
      // moved things forward by about what a tick can cover. A seek produces a jump far
      // outside that, and without this check dragging the scrubber near the end counted as
      // reaching it -- the episode was marked played and the queue moved on, mid-episode.
      //
      // Worse while an episode is still loading, because a partly-buffered stream can
      // report a duration shorter than the real one, putting the "end" somewhere in the
      // middle. So the element's duration is trusted only when it roughly agrees with what
      // the feed said; when it does not, the file is still arriving and this stays out of
      // the way until it settles.
      const arrivedByPlaying =
        previous !== null && audio.currentTime - previous > 0 &&
        audio.currentTime - previous <= MAX_TICK_SECONDS;

      const claimed = episodeRef.current?.duration_seconds ?? 0;
      const durationLooksSettled =
        Number.isFinite(audio.duration) &&
        audio.duration > margin &&
        (claimed <= 0 || audio.duration >= claimed * DURATION_AGREEMENT);

      if (
        !outroFiredRef.current &&
        !audio.paused &&
        !audio.seeking &&
        arrivedByPlaying &&
        durationLooksSettled &&
        audio.currentTime >= audio.duration - margin
      ) {
        outroFiredRef.current = true;
        finish();
      }
    };
    const onDuration = () => {
      if (Number.isFinite(audio.duration)) setDuration(audio.duration);
    };
    const onWaiting = () => setBuffering(true);
    const onPlaying = () => setBuffering(false);
    const onError = () => {
      setBuffering(false);
      setError("Playback failed. The episode may no longer be available from the publisher.");
    };
    const finish = () => {
      reportPosition(audio.duration || 0, { played: true, force: true });

      if (sleepAtEndRef.current) {
        // "Stop at the end of this episode" means this one, so it also cancels the
        // auto-advance that would otherwise start the next thing in the queue.
        sleepAtEndRef.current = false;
        setSleepAtEnd(false);
        audio.pause();
        setPlaying(false);
        return;
      }

      const next = advanceRef.current?.() ?? null;
      if (!next) {
        audio.pause();
        setPlaying(false);
        return;
      }

      // Deliberately no pause before handing over.
      //
      // Pausing ends the platform's audio session, and on iOS a backgrounded page is
      // suspended the moment audio stops -- so the code that would have started the next
      // episode does not run until the app is opened again. Assigning a new source to an
      // element that is still playing keeps one continuous session, which is what lets the
      // queue advance while the phone is in a pocket.
      play(next);
    };

    const onEnded = () => finish();

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
        new Blob(
          [
            JSON.stringify({
              position_seconds: Math.floor(audio.currentTime),
              // A beacon is queued by the browser and sent whenever it manages to; without
              // this it would arrive undated and overwrite whatever happened in between.
              changed_at: new Date().toISOString(),
            }),
          ],
          { type: "application/json" },
        ),
      );
    };
    window.addEventListener("pagehide", onUnload);
    return () => window.removeEventListener("pagehide", onUnload);
  }, []);

  // When the page last came to the foreground, for the staleness check below.
  const becameVisibleAtRef = useRef(Date.now());
  useEffect(() => {
    const mark = () => {
      if (document.visibilityState === "visible") becameVisibleAtRef.current = Date.now();
    };
    mark();
    document.addEventListener("visibilitychange", mark);
    // A page restored from the back/forward cache never fires visibilitychange.
    window.addEventListener("pageshow", mark);
    return () => {
      document.removeEventListener("visibilitychange", mark);
      window.removeEventListener("pageshow", mark);
    };
  }, []);

  /** Resume as asked by the platform rather than by the app's own controls. */
  const platformResume = useCallback(() => {
    if (
      !shouldHonorPlatformResume(
        Date.now(),
        becameVisibleAtRef.current,
        document.visibilityState === "visible",
      )
    ) {
      return;
    }
    resume();
  }, [resume]);

  rateRef.current = playbackRate;

  // Feeds carry the show name the lock screen shows as the artist, and the per-show speed
  // that cue() applies.
  const { data: feeds } = useQuery({ queryKey: ["feeds"], queryFn: api.feeds });
  const showTitle =
    feeds?.find((candidate) => candidate.id === episode?.feed_id)?.title ?? null;

  useEffect(() => {
    feedRatesRef.current = new Map(
      (feeds ?? []).map((feed) => [feed.id, feed.effective_playback_rate]),
    );
    feedSkipsRef.current = new Map(
      (feeds ?? []).map((feed) => [
        feed.id,
        { intro: feed.intro_skip_seconds, outro: feed.outro_skip_seconds },
      ]),
    );
  }, [feeds]);

  // Chapters for whatever is playing, so ad breaks can be skipped without the Now Playing
  // view being open. Fetched under the same key that view uses, so it is one request.
  const currentFeed = feeds?.find((feed) => feed.id === episode?.feed_id);
  const wantsSponsorSkip = currentFeed?.effective_skip_sponsor_chapters ?? false;

  const { data: chapterData } = useQuery({
    queryKey: ["chapters", episode?.id],
    queryFn: () => api.chapters(episode!.id),
    enabled: episode !== null && wantsSponsorSkip,
    staleTime: Infinity,
  });

  useEffect(() => {
    skipSponsorsRef.current = wantsSponsorSkip;
    chaptersRef.current = chapterData?.chapters ?? [];
  }, [wantsSponsorSkip, chapterData]);

  useMediaSession({
    // Null until playback has begun, so there is no session for a stale action to reach.
    episode: sessionActive ? episode : null,
    showTitle,
    playing,
    position,
    duration,
    playbackRate,
    resume: platformResume,
    pause,
    skip,
    seek,
    stop,
  });

  const value = useMemo(
    () => ({
      episode,
      playing,
      buffering,
      error,
      playbackRate,
      expanded,
      play,
      toggle,
      resume,
      pause,
      seek,
      skip,
      setPlaybackRate,
      setExpanded,
      stop,
      setAdvanceHandler,
      sleepMinutes,
      sleepAtEnd,
      setSleepTimer,
    }),
    [
      episode, playing, buffering, error, playbackRate, expanded,
      play, toggle, resume, pause, seek, skip, setPlaybackRate, stop, setAdvanceHandler,
      sleepMinutes, sleepAtEnd, setSleepTimer,
    ],
  );

  const progress = useMemo(() => ({ position, duration }), [position, duration]);

  return (
    <PlayerContext.Provider value={value}>
      <PlayerProgressContext.Provider value={progress}>
        {children}
      </PlayerProgressContext.Provider>
    </PlayerContext.Provider>
  );
}

export function usePlayerProgress(): PlayerProgressValue {
  const value = useContext(PlayerProgressContext);
  if (!value) throw new Error("usePlayerProgress must be used inside PlayerProvider");
  return value;
}

export function usePlayer(): PlayerValue {
  const value = useContext(PlayerContext);
  if (!value) throw new Error("usePlayer must be used inside PlayerProvider");
  return value;
}
