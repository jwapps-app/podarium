import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { Artwork } from "../components/Artwork";
import { EpisodeRow } from "../components/EpisodeRow";
import { RefreshIcon } from "../components/Icons";
import { Empty, ErrorNotice, Loading } from "../components/Loading";
import { formatRelativeExact } from "../lib/format";
import { isNewArrival } from "../lib/newness";
import { useEpisodes, useFeed, useFeedActions, useQueue, useSettings } from "../lib/queries";
import { toPlainText } from "../lib/sanitize";
import type { RetentionMode } from "../lib/types";

export function FeedDetailPage() {
  const { id } = useParams();
  const feedId = Number(id);
  const navigate = useNavigate();

  const { data: feed, isLoading, error } = useFeed(feedId);
  const { data: globals } = useSettings();
  const { data: queue } = useQueue();
  const actions = useFeedActions();

  const [showUnplayedOnly, setShowUnplayedOnly] = useState(false);
  const [showSettings, setShowSettings] = useState(false);

  const { data: episodes } = useEpisodes({
    feed_id: feedId,
    limit: 100,
    unplayed: showUnplayedOnly ? true : undefined,
  });

  // Opening the show is what clears its badge. Once per visit, and only after the feed has
  // actually loaded, so a failed load does not silently mark it seen.
  const markedRef = useRef<number | null>(null);
  const markSeen = actions.markSeen;
  useEffect(() => {
    if (!feed || markedRef.current === feed.id) return;
    if (!feed.new_episode_count) return;
    markedRef.current = feed.id;
    markSeen.mutate(feed.id);
  }, [feed, markSeen]);

  if (isLoading) return <Loading label="Loading show" />;
  if (error) return <ErrorNotice error={error} />;
  if (!feed) return <ErrorNotice error={new Error("Show not found")} />;

  const queuedIds = new Set((queue ?? []).map((item) => item.episode_id));

  const unsubscribe = (purge: boolean) => {
    const message = purge
      ? `Unsubscribe from "${feed.title ?? feed.feed_url}" and delete its downloaded audio?\n\nThis removes the show and its episode history.`
      : `Unsubscribe from "${feed.title ?? feed.feed_url}"?\n\nDownloaded audio is deleted along with it; use "Make inactive" instead if you want to keep everything.`;
    if (!window.confirm(message)) return;
    actions.unsubscribe.mutate({ id: feed.id, purge }, { onSuccess: () => navigate("/") });
  };

  return (
    <>
      <header className="feed-header">
        <Artwork
          className="feed-art"
          src={feed.image_url}
          alt={feed.title ?? feed.feed_url}
          fallbackText={feed.title}
        />

        <div className="feed-header-body">
          <h1 className="page-title">{feed.title ?? feed.feed_url}</h1>
          {feed.author ? <div className="feed-meta">{feed.author}</div> : null}

          <div className="feed-meta">
            {feed.episode_count ?? 0} episodes
            {feed.unplayed_count ? ` · ${feed.unplayed_count} unplayed` : null}
            {" · "}
            refreshed {formatRelativeExact(feed.last_fetched_at)}
            {feed.effective_auto_download_count > 0
              ? ` · auto-downloading ${feed.effective_auto_download_count} newest` +
                (feed.auto_download_count === null ? " (global)" : "")
              : null}
          </div>

          {feed.description ? (
            <p className="feed-description">{toPlainText(feed.description, 420)}</p>
          ) : null}

          <div className="feed-actions">
            <button
              className="btn"
              onClick={() => actions.refresh.mutate(feed.id)}
              disabled={actions.refresh.isPending}
            >
              <RefreshIcon style={{ width: 15, height: 15 }} />
              {actions.refresh.isPending ? "Refreshing…" : "Refresh now"}
            </button>
            <button className="btn" onClick={() => setShowSettings((value) => !value)}>
              {showSettings ? "Hide settings" : "Settings"}
            </button>
            <button className="btn btn-danger" onClick={() => unsubscribe(true)}>
              Unsubscribe
            </button>
          </div>
        </div>
      </header>

      {feed.fetch_error ? (
        <div className="notice notice-error" style={{ marginBottom: 18 }}>
          <strong>Last refresh failed</strong> ({feed.fetch_error_count} in a row, backing off).
          <div className="mono" style={{ marginTop: 6 }}>{feed.fetch_error}</div>
        </div>
      ) : null}

      {showSettings ? (
        <FeedSettings
          feed={feed}
          globalMode={globals?.global_retention_mode}
          globalDays={globals?.global_retention_days}
          globalAutoDownload={globals?.global_auto_download_count}
          onSave={(body) => actions.update.mutate({ id: feed.id, ...body })}
          saving={actions.update.isPending}
        />
      ) : null}

      <div className="filters" style={{ marginTop: 22 }}>
        <button
          className={`chip${showUnplayedOnly ? "" : " on"}`}
          onClick={() => setShowUnplayedOnly(false)}
        >
          All episodes
        </button>
        <button
          className={`chip${showUnplayedOnly ? " on" : ""}`}
          onClick={() => setShowUnplayedOnly(true)}
        >
          Unplayed
        </button>
      </div>

      {!episodes || episodes.items.length === 0 ? (
        <Empty title="No episodes">
          <p>Try refreshing the feed.</p>
        </Empty>
      ) : (
        <div className="episode-list">
          {episodes.items.map((episode) => (
            <EpisodeRow
              key={episode.id}
              episode={episode}
              queued={queuedIds.has(episode.id)}
              isNew={isNewArrival(episode, feed)}
            />
          ))}
        </div>
      )}
    </>
  );
}

interface SettingsProps {
  feed: {
    auto_download_count: number | null;
    retention_mode: RetentionMode | null;
    retention_days: number | null;
    active: boolean;
  };
  globalMode: RetentionMode | undefined;
  globalDays: number | undefined;
  globalAutoDownload: number | undefined;
  saving: boolean;
  onSave: (body: {
    auto_download_count?: number;
    retention_mode?: RetentionMode;
    retention_days?: number;
    active?: boolean;
    clear_retention_mode?: boolean;
    clear_retention_days?: boolean;
    clear_auto_download_count?: boolean;
  }) => void;
}

const MODE_LABELS: Record<RetentionMode, string> = {
  after_played: "Delete after played",
  after_download: "Delete after download",
  never: "Keep forever",
};

function FeedSettings({
  feed,
  globalMode,
  globalDays,
  globalAutoDownload,
  saving,
  onSave,
}: SettingsProps) {
  // "" is inherit, which is distinct from an explicit 0.
  const [autoDownload, setAutoDownload] = useState(
    feed.auto_download_count === null ? "" : String(feed.auto_download_count),
  );
  // "" is not the same as 0 here: it means inherit the global, which is a real state.
  const [mode, setMode] = useState<RetentionMode | "">(feed.retention_mode ?? "");
  const [days, setDays] = useState(feed.retention_days === null ? "" : String(feed.retention_days));
  const [active, setActive] = useState(feed.active);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    onSave({
      ...(autoDownload === ""
        ? { clear_auto_download_count: true }
        : { auto_download_count: Math.max(0, Number(autoDownload) || 0) }),
      active,
      ...(mode === "" ? { clear_retention_mode: true } : { retention_mode: mode }),
      ...(days === "" ? { clear_retention_days: true } : { retention_days: Number(days) }),
    });
  };

  return (
    <form className="panel" onSubmit={submit}>
      <div className="panel-title">Show settings</div>
      <p className="panel-hint">
        Anything left blank inherits the global setting
        {globalMode ? ` (${MODE_LABELS[globalMode].toLowerCase()}, ${globalDays} days)` : ""}.
      </p>

      <div className="field-row">
        <div className="field">
          <label htmlFor="auto">Auto-download newest</label>
          <input
            id="auto"
            type="number"
            min={0}
            placeholder={
              globalAutoDownload === undefined
                ? "Inherit global"
                : `Inherit global (${globalAutoDownload})`
            }
            value={autoDownload}
            onChange={(event) => setAutoDownload(event.target.value)}
          />
          <div className="field-hint">
            Blank follows the global default. Keeps exactly this many recent episodes on
            disk — lowering it removes the excess, and 0 reclaims everything. Queued
            episodes and hand-picked downloads are never removed, and a show set to keep
            forever is left alone.
          </div>
        </div>

        <div className="field">
          <label htmlFor="mode">Retention</label>
          <select id="mode" value={mode} onChange={(event) => setMode(event.target.value as RetentionMode | "")}>
            <option value="">Inherit global</option>
            <option value="after_played">Delete after played</option>
            <option value="after_download">Delete after download</option>
            <option value="never">Keep forever</option>
          </select>
        </div>

        <div className="field">
          <label htmlFor="days">Keep for (days)</label>
          <input
            id="days"
            type="number"
            min={0}
            placeholder="Inherit global"
            value={days}
            onChange={(event) => setDays(event.target.value)}
          />
        </div>
      </div>

      <label className="check" style={{ marginBottom: 16 }}>
        <input type="checkbox" checked={active} onChange={(event) => setActive(event.target.checked)} />
        Active — refresh this feed on schedule and show it in the library
      </label>

      <button className="btn btn-primary" type="submit" disabled={saving}>
        {saving ? "Saving…" : "Save settings"}
      </button>
    </form>
  );
}
