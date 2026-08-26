import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";

import { formatClock } from "../lib/format";
import { PLAYBACK_RATES, usePlayer } from "../lib/player";
import { useFeeds, useQueue } from "../lib/queries";
import type { Episode } from "../lib/types";
import { Artwork } from "./Artwork";
import { Back10Icon, ChevronUpIcon, Forward30Icon, PauseIcon, PlayIcon } from "./Icons";

export function PlayerBar() {
  const player = usePlayer();
  const { data: queue } = useQueue();
  const { data: feeds } = useFeeds();

  // Kept in a ref so the handler the player holds always sees the current queue without
  // being re-registered (and re-rendering the player) on every queue refetch.
  const queueRef = useRef(queue);
  queueRef.current = queue;

  const currentId = player.episode?.id;

  useEffect(() => {
    player.setAdvanceHandler((): Episode | null => {
      const items = queueRef.current ?? [];
      if (items.length === 0) return null;
      const index = items.findIndex((item) => item.episode_id === currentId);
      // Not in the queue: start at its head. Otherwise take the next item, if any.
      const next = index === -1 ? items[0] : items[index + 1];
      return next?.episode ?? null;
    });
    return () => player.setAdvanceHandler(null);
  }, [player, currentId]);

  if (!player.episode) return null;

  const episode = player.episode;
  const feed = feeds?.find((candidate) => candidate.id === episode.feed_id);
  const duration = player.duration || episode.duration_seconds || 0;
  const percent = duration > 0 ? (player.position / duration) * 100 : 0;

  const cycleRate = () => {
    const index = PLAYBACK_RATES.indexOf(player.playbackRate);
    // An unlisted rate (an older saved default, say) steps to the start rather than sticking.
    player.setPlaybackRate(PLAYBACK_RATES[(index + 1) % PLAYBACK_RATES.length]);
  };

  const playPause = (
    <button
      className="play-btn"
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
  );

  return (
    <div className="player" role="region" aria-label="Now playing">
      <div className="player-now">
        {/* The artwork and title open the full view; the show name stays a link to the
            feed, so both destinations are reachable from the bar. */}
        <button
          className="player-expand"
          onClick={() => player.setExpanded(true)}
          aria-label="Open now playing"
          title="Open now playing"
        >
          <Artwork
            className="player-art"
            src={episode.image_url}
            alt=""
            fallbackText={feed?.title ?? episode.title}
          />
          <span className="player-expand-hint" aria-hidden="true">
            <ChevronUpIcon />
          </span>
        </button>
        <div style={{ minWidth: 0 }}>
          <button
            className="player-title"
            onClick={() => player.setExpanded(true)}
            title={episode.title ?? ""}
          >
            {episode.title ?? "Untitled episode"}
          </button>
          <Link className="player-show" to={`/feeds/${episode.feed_id}`}>
            {feed?.title ?? "Unknown show"}
          </Link>
        </div>
      </div>

      <div className="player-center">
        <div className="player-buttons">
          <button className="btn-icon" onClick={() => player.skip(-10)} aria-label="Back 10 seconds" title="Back 10 seconds">
            <Back10Icon />
          </button>
          {playPause}
          <button className="btn-icon" onClick={() => player.skip(30)} aria-label="Forward 30 seconds" title="Forward 30 seconds">
            <Forward30Icon />
          </button>
        </div>

        <div className="player-scrub">
          <span className="player-time">{formatClock(player.position)}</span>
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
          <span className="player-time right">{formatClock(Math.max(duration - player.position, 0))}</span>
        </div>
      </div>

      <div className="player-right">
        {player.error ? (
          <span style={{ fontSize: 12, color: "var(--danger)" }}>{player.error}</span>
        ) : null}
        {/* The narrow-phone home for the play button: the centred cluster is hidden at this
            width, and the bar can now come up paused on launch holding what you were last
            listening to. Without this, resuming would mean knowing to open the full view
            first. Only one of the two is ever in the document -- the other is display:none,
            so it leaves the accessibility tree with it. */}
        <span className="player-play-compact">{playPause}</span>
        <button className="btn btn-sm rate-btn" onClick={cycleRate} title="Playback speed">
          {player.playbackRate}×
        </button>
        <button className="btn btn-sm" onClick={player.stop} title="Stop and close the player">
          Stop
        </button>
      </div>
    </div>
  );
}
