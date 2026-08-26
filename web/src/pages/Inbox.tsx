import { useEffect, useRef, useState } from "react";

import { EpisodeRow } from "../components/EpisodeRow";
import { Empty, ErrorNotice, Loading } from "../components/Loading";
import { isNewArrival } from "../lib/newness";
import { useEpisodes, useFeedActions, useFeeds, useQueue } from "../lib/queries";

type Filter = "all" | "unplayed" | "downloaded";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "unplayed", label: "Unplayed" },
  { key: "all", label: "All" },
  { key: "downloaded", label: "Downloaded" },
];

export function InboxPage() {
  const [filter, setFilter] = useState<Filter>("unplayed");

  const { data, isLoading, error } = useEpisodes({
    limit: 100,
    unplayed: filter === "unplayed" ? true : undefined,
    downloaded: filter === "downloaded" ? true : undefined,
  });
  const { data: feeds } = useFeeds();
  const { data: queue } = useQueue();

  // Opening the inbox clears the badge, the way opening a show clears its own. This
  // necessarily zeroes the library tiles too -- the nav badge is their sum, so there is no
  // clearing one without the other.
  //
  // The "new" tags on the rows below are unaffected: isNewArrival keys off the feed's
  // created_at, not its last_seen_at, so what you came to look at stays marked while you
  // read it.
  const { markAllSeen } = useFeedActions();
  const clearedRef = useRef(false);

  useEffect(() => {
    if (clearedRef.current || !feeds) return;
    if (!feeds.some((feed) => feed.active && (feed.new_episode_count ?? 0) > 0)) return;
    clearedRef.current = true;
    markAllSeen.mutate();
  }, [feeds, markAllSeen]);

  const feedsById = new Map((feeds ?? []).map((feed) => [feed.id, feed]));
  const queuedIds = new Set((queue ?? []).map((item) => item.episode_id));

  return (
    <>
      <header className="page-head">
        <div>
          <h1 className="page-title">Inbox</h1>
          <p className="page-subtitle">Every show, newest first by publication date.</p>
        </div>
      </header>

      <div className="filters">
        {FILTERS.map((option) => (
          <button
            key={option.key}
            className={`chip${filter === option.key ? " on" : ""}`}
            onClick={() => setFilter(option.key)}
          >
            {option.label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <Loading label="Loading episodes" />
      ) : error ? (
        <ErrorNotice error={error} />
      ) : !data || data.items.length === 0 ? (
        <Empty title="Nothing here">
          <p>
            {filter === "unplayed"
              ? "You are all caught up."
              : "No episodes match this filter yet."}
          </p>
        </Empty>
      ) : (
        <div className="episode-list">
          {data.items.map((episode) => (
            <EpisodeRow
              key={episode.id}
              episode={episode}
              showTitle={feedsById.get(episode.feed_id)?.title ?? null}
              queued={queuedIds.has(episode.id)}
              isNew={isNewArrival(episode, feedsById.get(episode.feed_id))}
            />
          ))}
        </div>
      )}
    </>
  );
}
