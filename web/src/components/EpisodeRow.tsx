import { useState } from "react";
import { Link } from "react-router-dom";

import { formatBytes, formatDate, formatDuration } from "../lib/format";
import { usePlayer } from "../lib/player";
import { useEpisodeActions } from "../lib/queries";
import { sanitizeHtml } from "../lib/sanitize";
import { podlinkEpisodeUrl } from "../lib/share";
import type { Episode } from "../lib/types";
import { Artwork } from "./Artwork";
import { ShareButton } from "./ShareButton";
import {
  CheckIcon,
  DownloadIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  StarIcon,
  TrashIcon,
} from "./Icons";

interface Props {
  episode: Episode;
  /** Feed title, when the row is shown outside its own feed page. */
  showTitle?: string | null;
  /** Needed to build the share link; the pod.link URL is keyed to the feed. */
  feedUrl?: string | null;
  queued?: boolean;
  /** An episode first seen in the last day gets a "new" tag. Keyed off first_seen_at,
   *  never published_at -- a publisher re-stamping pubDate must not light up the inbox. */
  isNew?: boolean;
}

export function EpisodeRow({ episode, showTitle, feedUrl, queued, isNew }: Props) {
  const player = usePlayer();
  const actions = useEpisodeActions();
  const [expanded, setExpanded] = useState(false);
  const shareUrl = podlinkEpisodeUrl(feedUrl, episode.guid);

  const isCurrent = player.episode?.id === episode.id;
  const isPlaying = isCurrent && player.playing;

  const progress =
    episode.duration_seconds && episode.position_seconds > 0 && !episode.played
      ? Math.min(100, (episode.position_seconds / episode.duration_seconds) * 100)
      : 0;

  const busy =
    actions.download.isPending || actions.removeDownload.isPending || actions.enqueue.isPending;

  return (
    <article
      className={[
        "episode",
        isCurrent ? "is-current" : "",
        episode.played ? "is-played" : "",
      ].join(" ").trim()}
    >
      <Artwork
        className="episode-art"
        src={episode.image_url}
        alt=""
        fallbackText={showTitle ?? episode.title}
      />

      <div className="episode-body">
        {showTitle ? (
          <Link className="episode-show" to={`/feeds/${episode.feed_id}`}>
            {showTitle}
          </Link>
        ) : null}

        <h3
          className="episode-title"
          onClick={() => setExpanded((value) => !value)}
          role="button"
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              setExpanded((value) => !value);
            }
          }}
        >
          {episode.title ?? "Untitled episode"}
        </h3>

        <div className="episode-meta">
          {isNew && !episode.played ? <span className="tag tag-new">new</span> : null}
          {episode.downloaded ? <span className="tag tag-downloaded">downloaded</span> : null}
          <span>{formatDate(episode.published_at)}</span>
          {episode.duration_seconds ? (
            <>
              <span className="dot">·</span>
              <span>{formatDuration(episode.duration_seconds)}</span>
            </>
          ) : null}
          {episode.downloaded && episode.local_bytes ? (
            <>
              <span className="dot">·</span>
              <span>{formatBytes(episode.local_bytes)}</span>
            </>
          ) : null}
          {episode.played ? (
            <>
              <span className="dot">·</span>
              <span>played</span>
            </>
          ) : null}
        </div>

        {progress > 0 ? (
          <div className="episode-progress" aria-hidden="true">
            <span style={{ width: `${progress}%` }} />
          </div>
        ) : null}

        {expanded ? (
          <div
            className="episode-notes"
            /* Sanitised in sanitize.ts: every subresource-fetching element is stripped so
               show notes cannot pull an image straight from a publisher CDN. */
            dangerouslySetInnerHTML={{ __html: sanitizeHtml(episode.description_html) }}
          />
        ) : null}
      </div>

      <div className="episode-actions">
        <button
          className="btn-icon"
          title={isPlaying ? "Pause" : "Play"}
          aria-label={isPlaying ? "Pause" : "Play"}
          onClick={() => (isCurrent ? player.toggle() : player.play(episode))}
        >
          {isPlaying ? <PauseIcon /> : <PlayIcon />}
        </button>

        <button
          className={`btn-icon${queued ? " on" : ""}`}
          title={queued ? "Remove from queue" : "Add to queue"}
          aria-label={queued ? "Remove from queue" : "Add to queue"}
          disabled={busy}
          onClick={() =>
            queued
              ? actions.dequeue.mutate(episode.id)
              : actions.enqueue.mutate(episode.id)
          }
        >
          <PlusIcon />
        </button>

        <button
          className={`btn-icon${episode.downloaded ? " on" : ""}`}
          title={episode.downloaded ? "Delete downloaded file" : "Download"}
          aria-label={episode.downloaded ? "Delete downloaded file" : "Download"}
          disabled={busy}
          onClick={() =>
            episode.downloaded
              ? actions.removeDownload.mutate(episode.id)
              : actions.download.mutate(episode.id)
          }
        >
          {episode.downloaded ? <TrashIcon /> : <DownloadIcon />}
        </button>

        {shareUrl ? (
          <ShareButton
            url={shareUrl}
            title={episode.title ?? "Episode"}
            showTitle={showTitle}
          />
        ) : null}

        <button
          className={`btn-icon${episode.starred ? " on" : ""}`}
          title={episode.starred ? "Unstar" : "Star"}
          aria-label={episode.starred ? "Unstar" : "Star"}
          onClick={() => actions.setState.mutate({ id: episode.id, starred: !episode.starred })}
        >
          <StarIcon filled={episode.starred} />
        </button>

        <button
          className={`btn-icon${episode.played ? " on" : ""}`}
          title={episode.played ? "Mark unplayed" : "Mark played"}
          aria-label={episode.played ? "Mark unplayed" : "Mark played"}
          onClick={() => actions.setState.mutate({ id: episode.id, played: !episode.played })}
        >
          <CheckIcon />
        </button>
      </div>
    </article>
  );
}
