import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";

import { formatClock } from "../lib/format";
import { PLAYBACK_RATES, usePlayer } from "../lib/player";
import { useFeeds, useQueue } from "../lib/queries";
import type { Episode } from "../lib/types";
import { Artwork } from "./Artwork";
import { Back15Icon, Forward30Icon, PauseIcon, PlayIcon } from "./Icons";

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

  return (
    <div className="player" role="region" aria-label="Now playing">
      <div className="player-now">
        <Artwork
          className="player-art"
          src={episode.image_url}
          alt=""
          fallbackText={feed?.title ?? episode.title}
        />
        <div style={{ minWidth: 0 }}>
          <div className="player-title" title={episode.title ?? ""}>
            {episode.title ?? "Untitled episode"}
          </div>
          <Link className="player-show" to={`/feeds/${episode.feed_id}`}>
            {feed?.title ?? "Unknown show"}
          </Link>
        </div>
      </div>

      <div className="player-center">
        <div className="player-buttons">
          <button className="btn-icon" onClick={() => player.skip(-15)} aria-label="Back 15 seconds" title="Back 15s">
            <Back15Icon />
          </button>
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
          <button className="btn-icon" onClick={() => player.skip(30)} aria-label="Forward 30 seconds" title="Forward 30s">
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
        <button className="btn btn-sm rate-btn" onClick={cycleRate} title="Playback speed">
          {player.playbackRate}×
        </button>
        <button className="btn btn-sm" onClick={player.stop} title="Close player">
          Close
        </button>
      </div>
    </div>
  );
}
