import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { nextChapterTarget, previousChapterTarget } from "../lib/chapters";
import { formatClock, formatDate, formatDuration } from "../lib/format";
import { PLAYBACK_RATES, usePlayer, usePlayerProgress } from "../lib/player";
import { useEpisodeActions, useFeeds, useQueue } from "../lib/queries";
import { sanitizeHtml } from "../lib/sanitize";
import { podlinkEpisodeUrl } from "../lib/share";
import { Artwork } from "./Artwork";
import { ShareButton } from "./ShareButton";
import {
  Back10Icon,
  CheckIcon,
  ChevronDownIcon,
  DownloadIcon,
  Forward30Icon,
  NextChapterIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  PrevChapterIcon,
  StarIcon,
  TrashIcon,
} from "./Icons";

/** The full listening view, over the page rather than on its own route.
 *
 *  Deliberately not a route: opening it should not lose your place in the list you were
 *  browsing, and closing it should not push a history entry you have to back out of.
 *  Escape and the chevron both dismiss it.
 */
export function NowPlaying() {
  const player = usePlayer();
  const progress = usePlayerProgress();
  const actions = useEpisodeActions();
  const { data: feeds } = useFeeds();
  const { data: queue } = useQueue();

  const episode = player.episode;
  const open = player.expanded && episode !== null;

  // Lists arrive without show notes, so an episode played from one carries none; fetch
  // the full record when the view is open. Hooks run before the early return below.
  const { data: full } = useQuery({
    queryKey: ["episode", episode?.id],
    queryFn: () => api.episode(episode!.id),
    enabled: open && episode !== null && episode.description_html == null,
    staleTime: 5 * 60_000,
  });
  const notesHtml = episode?.description_html ?? full?.description_html;

  // Same query key as the list in the side panel, so this is one fetch, not two.
  const { data: chapterData } = useQuery({
    queryKey: ["chapters", episode?.id],
    queryFn: () => api.chapters(episode!.id),
    enabled: open && episode !== null,
    staleTime: Infinity,
  });
  const chapters = chapterData?.chapters ?? [];

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") player.setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    // The page behind must not scroll while this is over it.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [open, player]);

  if (!open || !episode) return null;

  const feed = feeds?.find((candidate) => candidate.id === episode.feed_id);
  const duration = progress.duration || episode.duration_seconds || 0;
  const percent = duration > 0 ? (progress.position / duration) * 100 : 0;
  const queued = new Set((queue ?? []).map((item) => item.episode_id)).has(episode.id);
  const shareUrl = podlinkEpisodeUrl(feed?.feed_url, episode.guid);

  const upNext = (queue ?? [])
    .filter((item) => item.episode_id !== episode.id)
    .slice(0, 4);

  const cycleRate = () => {
    const index = PLAYBACK_RATES.indexOf(player.playbackRate);
    player.setPlaybackRate(PLAYBACK_RATES[(index + 1) % PLAYBACK_RATES.length]);
  };

  return (
    <div className="now-playing" role="dialog" aria-modal="true" aria-label="Now playing">
      <header className="np-head">
        <button
          className="btn-icon"
          onClick={() => player.setExpanded(false)}
          aria-label="Close now playing"
          title="Close"
        >
          <ChevronDownIcon />
        </button>
        <span className="np-eyebrow">Now playing</span>
        <button className="btn btn-sm" onClick={player.stop}>Stop</button>
      </header>

      <div className="np-body">
        <div className="np-main">
          <Artwork
            className="np-art"
            src={episode.image_url}
            alt=""
            fallbackText={feed?.title ?? episode.title}
          />

          <div className="np-meta">
            {feed ? (
              <Link
                className="np-show"
                to={`/feeds/${episode.feed_id}`}
                onClick={() => player.setExpanded(false)}
              >
                {feed.title ?? "Unknown show"}
              </Link>
            ) : null}
            <h1 className="np-title">{episode.title ?? "Untitled episode"}</h1>
            <div className="np-sub">
              {formatDate(episode.published_at)}
              {episode.duration_seconds ? ` · ${formatDuration(episode.duration_seconds)}` : ""}
              {episode.downloaded ? " · downloaded" : " · streaming"}
            </div>
          </div>

          <div className="np-scrub">
            <input
              className="scrubber"
              type="range"
              min={0}
              max={Math.max(duration, 1)}
              step={1}
              value={Math.min(progress.position, duration || 1)}
              style={{ ["--progress" as string]: `${percent}%` }}
              onChange={(event) => player.seek(Number(event.target.value))}
              aria-label="Seek"
            />
            <div className="np-times">
              <span>{formatClock(progress.position)}</span>
              <span>−{formatClock(Math.max(duration - progress.position, 0))}</span>
            </div>
          </div>

          <div className="np-transport">
            {/* Only for episodes that actually have chapters. Two controls that do nothing
                on all but a handful of episodes are clutter, not affordance. */}
            {chapters.length > 1 ? (
              <button
                className="btn-icon np-skip"
                onClick={() => {
                  const target = previousChapterTarget(chapters, progress.position);
                  if (target !== null) player.seek(target);
                }}
                aria-label="Previous chapter"
                title="Previous chapter"
              >
                <PrevChapterIcon />
              </button>
            ) : null}

            <button
              className="btn-icon np-skip"
              onClick={() => player.skip(-10)}
              aria-label="Back 10 seconds"
              title="Back 10 seconds"
            >
              <Back10Icon />
            </button>

            <button
              className="np-play"
              onClick={player.toggle}
              aria-label={player.playing ? "Pause" : "Play"}
            >
              {player.buffering ? (
                <span className="spinner" style={{ borderTopColor: "currentColor" }} />
              ) : player.playing ? (
                <PauseIcon />
              ) : (
                <PlayIcon />
              )}
            </button>

            <button
              className="btn-icon np-skip"
              onClick={() => player.skip(30)}
              aria-label="Forward 30 seconds"
              title="Forward 30 seconds"
            >
              <Forward30Icon />
            </button>

            {chapters.length > 1 ? (
              <button
                className="btn-icon np-skip"
                // Disabled in the last chapter rather than hidden, so the row does not
                // reflow under your thumb as the episode plays past the final boundary.
                disabled={nextChapterTarget(chapters, progress.position) === null}
                onClick={() => {
                  const target = nextChapterTarget(chapters, progress.position);
                  if (target !== null) player.seek(target);
                }}
                aria-label="Next chapter"
                title="Next chapter"
              >
                <NextChapterIcon />
              </button>
            ) : null}
          </div>

          <div className="np-actions">
            <button className="btn btn-sm rate-btn" onClick={cycleRate} title="Playback speed">
              {player.playbackRate}&times;
            </button>
            <SleepTimerButton />
            <button
              className={`btn-icon${queued ? " on" : ""}`}
              onClick={() =>
                queued ? actions.dequeue.mutate(episode.id) : actions.enqueue.mutate(episode.id)
              }
              aria-label={queued ? "Remove from queue" : "Add to queue"}
              title={queued ? "Remove from queue" : "Add to queue"}
            >
              <PlusIcon />
            </button>
            <button
              className={`btn-icon${episode.downloaded ? " on" : ""}`}
              onClick={() =>
                episode.downloaded
                  ? actions.removeDownload.mutate(episode.id)
                  : actions.download.mutate(episode.id)
              }
              aria-label={episode.downloaded ? "Delete downloaded file" : "Download"}
              title={episode.downloaded ? "Delete downloaded file" : "Download"}
            >
              {episode.downloaded ? <TrashIcon /> : <DownloadIcon />}
            </button>
            {shareUrl ? (
              <ShareButton
                url={shareUrl}
                title={episode.title ?? "Episode"}
                showTitle={feed?.title}
              />
            ) : null}

            <button
              className={`btn-icon${episode.starred ? " on" : ""}`}
              onClick={() => actions.setState.mutate({ id: episode.id, starred: !episode.starred })}
              aria-label={episode.starred ? "Unstar" : "Star"}
              title={episode.starred ? "Unstar" : "Star"}
            >
              <StarIcon filled={episode.starred} />
            </button>
            <button
              className={`btn-icon${episode.played ? " on" : ""}`}
              onClick={() => actions.setState.mutate({ id: episode.id, played: !episode.played })}
              aria-label={episode.played ? "Mark unplayed" : "Mark played"}
              title={episode.played ? "Mark unplayed" : "Mark played"}
            >
              <CheckIcon />
            </button>
          </div>

          {player.error ? <div className="notice notice-error">{player.error}</div> : null}
        </div>

        <aside className="np-side">
          {upNext.length > 0 ? (
            <section className="np-section">
              <h2 className="np-section-title">Up next</h2>
              {upNext.map((item) => (
                <button
                  key={item.episode_id}
                  className="np-next"
                  onClick={() => player.play(item.episode)}
                >
                  <Artwork
                    className="np-next-art"
                    src={item.episode.image_url}
                    alt=""
                    fallbackText={item.episode.title}
                  />
                  <span className="np-next-title">{item.episode.title ?? "Untitled"}</span>
                </button>
              ))}
            </section>
          ) : null}

          <ChapterList episodeId={episode.id} />

          {notesHtml ? (
            <section className="np-section">
              <h2 className="np-section-title">Show notes</h2>
              <div
                className="episode-notes"
                /* Sanitised: an <img> in a publisher's notes would fetch from their CDN. */
                dangerouslySetInnerHTML={{ __html: sanitizeHtml(notesHtml) }}
              />
            </section>
          ) : null}
        </aside>
      </div>
    </div>
  );
}

const SLEEP_CHOICES = [5, 15, 30, 45, 60] as const;

/** Sleep timer. Its own popover, because five options do not belong in the transport row. */
function SleepTimerButton() {
  const player = usePlayer();
  const [open, setOpen] = useState(false);

  const active = player.sleepMinutes !== null || player.sleepAtEnd;
  const label = player.sleepAtEnd ? "End" : player.sleepMinutes !== null ? `${player.sleepMinutes}m` : null;

  const choose = (value: number | "episode" | null) => {
    player.setSleepTimer(value);
    setOpen(false);
  };

  return (
    <div className="sleep-wrap">
      <button
        className={`btn btn-sm${active ? " on" : ""}`}
        onClick={() => setOpen((value) => !value)}
        title="Sleep timer"
        aria-expanded={open}
      >
        {active ? `Sleep ${label}` : "Sleep"}
      </button>

      {open ? (
        <div className="sleep-menu" role="menu">
          {SLEEP_CHOICES.map((minutes) => (
            <button key={minutes} role="menuitem" onClick={() => choose(minutes)}>
              {minutes} minutes
            </button>
          ))}
          <button role="menuitem" onClick={() => choose("episode")}>
            End of episode
          </button>
          {active ? (
            <button role="menuitem" className="sleep-cancel" onClick={() => choose(null)}>
              Cancel timer
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** Chapters, when the show publishes them.
 *
 *  Renders nothing at all when it has none, which is most shows -- an empty "Chapters"
 *  heading on every episode would be worse than no feature.
 */
function ChapterList({ episodeId }: { episodeId: number }) {
  const player = usePlayer();
  const progress = usePlayerProgress();
  const { data } = useQuery({
    queryKey: ["chapters", episodeId],
    queryFn: () => api.chapters(episodeId),
    // The server caches the fetch; there is no reason to ask twice in a session.
    staleTime: Infinity,
  });

  const chapters = data?.chapters ?? [];
  if (chapters.length === 0) return null;

  // The chapter containing the playhead, i.e. the last one that has started.
  let currentIndex = -1;
  chapters.forEach((chapter, index) => {
    if (progress.position >= chapter.start_seconds) currentIndex = index;
  });

  return (
    <section className="np-section">
      <h2 className="np-section-title">Chapters</h2>
      {chapters.map((chapter, index) => (
        <button
          key={`${chapter.start_seconds}-${index}`}
          className={`np-chapter${index === currentIndex ? " on" : ""}`}
          onClick={() => player.seek(chapter.start_seconds)}
        >
          <span className="np-chapter-time mono">{formatClock(chapter.start_seconds)}</span>
          <span className="np-chapter-title">{chapter.title ?? `Chapter ${index + 1}`}</span>
        </button>
      ))}
    </section>
  );
}
