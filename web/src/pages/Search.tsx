import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { Artwork } from "../components/Artwork";
import { Empty, ErrorNotice, Loading } from "../components/Loading";
import { ApiError, api } from "../lib/api";
import { useFeedActions } from "../lib/queries";
import { formatDate, formatDuration } from "../lib/format";
import { toPlainText } from "../lib/sanitize";
import type { SearchResult } from "../lib/types";

export function SearchPage() {
  const navigate = useNavigate();
  const feedActions = useFeedActions();

  const [term, setTerm] = useState("");
  const [feedUrl, setFeedUrl] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [searchError, setSearchError] = useState<ApiError | Error | null>(null);
  // The feed URL being looked at, or null. Held here rather than routed, so closing the
  // preview returns to the results you searched for instead of losing them.
  const [previewing, setPreviewing] = useState<string | null>(null);

  const search = useMutation({
    mutationFn: (q: string) => api.search(q),
    onSuccess: (data) => {
      setResults(data);
      setSearchError(null);
    },
    onError: (error: Error) => {
      setResults(null);
      setSearchError(error);
    },
  });

  const resolve = useMutation({
    mutationFn: (url: string) => api.resolveFeedUrl(url),
    onSuccess: (data) => {
      setResults([data]);
      setSearchError(null);
    },
    onError: (error: Error) => {
      setResults(null);
      setSearchError(error);
    },
  });

  const subscribe = (result: SearchResult) => {
    feedActions.subscribe.mutate(
      result.podcast_index_id
        ? { podcast_index_id: result.podcast_index_id, feed_url: result.feed_url }
        : { feed_url: result.feed_url },
      { onSuccess: (feed) => navigate(`/feeds/${feed.id}`) },
    );
  };

  const searchUnavailable = searchError instanceof ApiError && searchError.isServiceUnavailable;

  return (
    <>
      <header className="page-head">
        <div>
          <h1 className="page-title">Add a show</h1>
          <p className="page-subtitle">
            Search runs through Podcast Index. Apple is never contacted.
          </p>
        </div>
      </header>

      <form
        className="panel"
        onSubmit={(event) => {
          event.preventDefault();
          if (term.trim()) search.mutate(term.trim());
        }}
      >
        <div className="panel-title">Search by name</div>
        <div className="field-row" style={{ alignItems: "flex-end" }}>
          <div className="field" style={{ marginBottom: 0 }}>
            <input
              value={term}
              placeholder="e.g. Hard Fork"
              onChange={(event) => setTerm(event.target.value)}
              aria-label="Search podcasts"
            />
          </div>
          <button className="btn btn-primary" type="submit" disabled={search.isPending || !term.trim()}>
            {search.isPending ? "Searching…" : "Search"}
          </button>
        </div>
      </form>

      <form
        className="panel"
        onSubmit={(event) => {
          event.preventDefault();
          if (feedUrl.trim()) resolve.mutate(feedUrl.trim());
        }}
      >
        <div className="panel-title">Or paste a feed URL</div>
        <p className="panel-hint">
          Works without Podcast Index credentials, and is the way to add a private or
          unlisted feed.
        </p>
        <div className="field-row" style={{ alignItems: "flex-end" }}>
          <div className="field" style={{ marginBottom: 0 }}>
            <input
              value={feedUrl}
              placeholder="https://example.com/feed.xml"
              onChange={(event) => setFeedUrl(event.target.value)}
              aria-label="Feed URL"
            />
          </div>
          <button className="btn" type="submit" disabled={resolve.isPending || !feedUrl.trim()}>
            {resolve.isPending ? "Checking…" : "Look up"}
          </button>
        </div>
      </form>

      {searchUnavailable ? (
        <div className="notice notice-warn" style={{ marginTop: 18 }}>
          <strong>Search is not configured.</strong> Podcast Index needs a free API key —
          set <span className="mono">PODCASTINDEX_KEY</span> and{" "}
          <span className="mono">PODCASTINDEX_SECRET</span> on the server. Pasting a feed
          URL above works without one.
        </div>
      ) : searchError ? (
        <div className="notice notice-error" style={{ marginTop: 18 }}>
          {searchError.message}
        </div>
      ) : null}

      {feedActions.subscribe.error ? (
        <div className="notice notice-error" style={{ marginTop: 18 }}>
          {(feedActions.subscribe.error as Error).message}
        </div>
      ) : null}

      {results ? (
        results.length === 0 ? (
          <div style={{ marginTop: 22 }}>
            <Empty title="No matches">
              <p>Try a different spelling, or paste the feed URL directly.</p>
            </Empty>
          </div>
        ) : (
          <div style={{ marginTop: 22 }}>
            <h2 className="panel-title" style={{ marginBottom: 12 }}>
              {results.length} {results.length === 1 ? "result" : "results"}
            </h2>
            <div className="episode-list">
              {results.map((result) => (
                <article className="episode" key={result.feed_url}>
                  {/* Server-proxied, like every other image here. Search results would
                      otherwise have the browser fetch from each publisher's CDN. */}
                  <Artwork
                    className="episode-art"
                    src={result.image_url}
                    alt=""
                    fallbackText={result.title}
                  />
                  <div className="episode-body">
                    <button
                      className="episode-title"
                      onClick={() => setPreviewing(result.feed_url)}
                      title="Look at this show before subscribing"
                    >
                      {result.title ?? result.feed_url}
                    </button>
                    <div className="episode-meta">
                      {result.author ? <span>{result.author}</span> : null}
                      {result.episode_count ? (
                        <>
                          <span className="dot">·</span>
                          <span>{result.episode_count} episodes</span>
                        </>
                      ) : null}
                    </div>
                    {result.description ? (
                      <p style={{ margin: "6px 0 0", color: "var(--text-muted)", fontSize: 13.5 }}>
                        {toPlainText(result.description, 220)}
                      </p>
                    ) : null}
                  </div>
                  <div className="episode-actions" style={{ alignItems: "center", gap: 6 }}>
                    {/* Details sits before Subscribe deliberately: subscribing is the
                        commitment, and the button you reach first should be the one that
                        only shows you something. */}
                    <button className="btn btn-sm" onClick={() => setPreviewing(result.feed_url)}>
                      Details
                    </button>
                    {result.already_subscribed ? (
                      <span className="tag">Subscribed</span>
                    ) : (
                      <button
                        className="btn btn-sm btn-primary"
                        onClick={() => subscribe(result)}
                        disabled={feedActions.subscribe.isPending}
                      >
                        {feedActions.subscribe.isPending ? "Adding…" : "Subscribe"}
                      </button>
                    )}
                  </div>
                </article>
              ))}
            </div>
          </div>
        )
      ) : null}

      {previewing ? (
        <PreviewPanel
          feedUrl={previewing}
          onClose={() => setPreviewing(null)}
          onSubscribe={(url) => {
            subscribe({ feed_url: url } as SearchResult);
            setPreviewing(null);
          }}
          subscribing={feedActions.subscribe.isPending}
        />
      ) : null}
    </>
  );
}

/** A show, before you commit to it.
 *
 *  Over the page rather than on a route, for the same reason Now Playing is: opening it
 *  should not lose the search results underneath, and closing it should not mean backing
 *  out of a history entry.
 */
function PreviewPanel({
  feedUrl,
  onClose,
  onSubscribe,
  subscribing,
}: {
  feedUrl: string;
  onClose: () => void;
  onSubscribe: (feedUrl: string) => void;
  subscribing: boolean;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["preview", feedUrl],
    queryFn: () => api.preview(feedUrl),
    // The server fetches the publisher's feed to answer this, so looking at the same show
    // twice in one session should not fetch it twice.
    staleTime: 5 * 60_000,
    retry: false,
  });

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="preview-scrim" onClick={onClose}>
      <div
        className="preview"
        role="dialog"
        aria-label="Show details"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="preview-head">
          <button className="btn btn-sm" onClick={onClose}>
            Close
          </button>
          {data && !data.already_subscribed ? (
            <button
              className="btn btn-sm btn-primary"
              disabled={subscribing}
              onClick={() => onSubscribe(data.feed_url)}
            >
              {subscribing ? "Adding…" : "Subscribe"}
            </button>
          ) : data ? (
            <span className="tag">Subscribed</span>
          ) : null}
        </header>

        {isLoading ? (
          <Loading label="Loading show" />
        ) : error ? (
          <ErrorNotice error={error} />
        ) : !data ? null : (
          <>
            <div className="preview-top">
              <Artwork
                className="preview-art"
                src={data.image_url}
                alt=""
                fallbackText={data.title}
              />
              <div style={{ minWidth: 0 }}>
                <h1 className="page-title">{data.title ?? data.feed_url}</h1>
                <div className="episode-meta">
                  {data.author ? <span>{data.author}</span> : null}
                  <span className="dot">·</span>
                  <span>{data.episode_count} episodes</span>
                </div>
                {data.description ? (
                  <p className="preview-description">{toPlainText(data.description, 700)}</p>
                ) : null}
              </div>
            </div>

            <h2 className="np-section-title" style={{ marginTop: 20 }}>
              Recent episodes
            </h2>
            {data.episodes.map((episode) => (
              <div className="preview-episode" key={episode.guid}>
                <div className="preview-episode-title">{episode.title ?? "Untitled"}</div>
                <div className="episode-meta">
                  {episode.published_at ? <span>{formatDate(episode.published_at)}</span> : null}
                  {episode.duration_seconds ? (
                    <>
                      <span className="dot">·</span>
                      <span>{formatDuration(episode.duration_seconds)}</span>
                    </>
                  ) : null}
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
