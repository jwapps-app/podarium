import { useEffect } from "react";
import { Link } from "react-router-dom";

import { formatClock, formatDate, formatDuration } from "../lib/format";
import { PLAYBACK_RATES, usePlayer } from "../lib/player";
import { useEpisodeActions, useFeeds, useQueue } from "../lib/queries";
import { sanitizeHtml } from "../lib/sanitize";
import { Artwork } from "./Artwork";
import {
  Back10Icon,
  CheckIcon,
  ChevronDownIcon,
  DownloadIcon,
  Forward30Icon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
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
  const actions = useEpisodeActions();
  const { data: feeds } = useFeeds();
  const { data: queue } = useQueue();

  const episode = player.episode;
  const open = player.expanded && episode !== null;

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
  const duration = player.duration || episode.duration_seconds || 0;
  const percent = duration > 0 ? (player.position / duration) * 100 : 0;
  const queued = new Set((queue ?? []).map((item) => item.episode_id)).has(episode.id);

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
              value={Math.min(player.position, duration || 1)}
              style={{ ["--progress" as string]: `${percent}%` }}
              onChange={(event) => player.seek(Number(event.target.value))}
              aria-label="Seek"
            />
            <div className="np-times">
              <span>{formatClock(player.position)}</span>
              <span>−{formatClock(Math.max(duration - player.position, 0))}</span>
            </div>
          </div>

          <div className="np-transport">
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
          </div>

          <div className="np-actions">
            <button className="btn btn-sm rate-btn" onClick={cycleRate} title="Playback speed">
              {player.playbackRate}&times;
            </button>
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

          {episode.description_html ? (
            <section className="np-section">
              <h2 className="np-section-title">Show notes</h2>
              <div
                className="episode-notes"
                /* Sanitised: an <img> in a publisher's notes would fetch from their CDN. */
                dangerouslySetInnerHTML={{ __html: sanitizeHtml(episode.description_html) }}
              />
            </section>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
