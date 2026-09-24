import { EpisodeRow } from "../components/EpisodeRow";
import { Empty, ErrorNotice, LoadMore, Loading } from "../components/Loading";
import { useEpisodePages, useFeeds, useQueue } from "../lib/queries";

/** Everything you have starred, across every show.
 *
 *  Includes shows you have since unsubscribed: starring is an explicit "keep this", so
 *  unsubscribing should not quietly empty the list.
 */
export function StarredPage() {
  const { items, isLoading, error, hasNextPage, fetchNextPage, isFetchingNextPage } =
    useEpisodePages({ starred: true, limit: 200 });
  const { data: feeds } = useFeeds();
  const { data: queue } = useQueue();

  const feedsById = new Map((feeds ?? []).map((feed) => [feed.id, feed]));
  const queuedIds = new Set((queue ?? []).map((item) => item.episode_id));

  return (
    <>
      <header className="page-head">
        <div>
          <h1 className="page-title">Starred</h1>
          <p className="page-subtitle">
            Kept indefinitely — retention never deletes a starred episode.
          </p>
        </div>
      </header>

      {isLoading ? (
        <Loading label="Loading starred episodes" />
      ) : error ? (
        <ErrorNotice error={error} />
      ) : items.length === 0 ? (
        <Empty title="Nothing starred yet">
          <p>
            Star an episode to keep it. Its audio stays on disk whatever the retention
            settings say.
          </p>
        </Empty>
      ) : (
        <div className="episode-list">
          {items.map((episode) => (
            <EpisodeRow
              key={episode.id}
              episode={episode}
              showTitle={feedsById.get(episode.feed_id)?.title ?? null}
              feedUrl={feedsById.get(episode.feed_id)?.feed_url}
              queued={queuedIds.has(episode.id)}
            />
          ))}
          <LoadMore hasMore={hasNextPage} loading={isFetchingNextPage} onMore={() => void fetchNextPage()} />
        </div>
      )}
    </>
  );
}
