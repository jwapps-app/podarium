import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { Artwork } from "../components/Artwork";
import { Empty } from "../components/Loading";
import { ApiError, api } from "../lib/api";
import { useFeedActions } from "../lib/queries";
import { toPlainText } from "../lib/sanitize";
import type { SearchResult } from "../lib/types";

export function SearchPage() {
  const navigate = useNavigate();
  const feedActions = useFeedActions();

  const [term, setTerm] = useState("");
  const [feedUrl, setFeedUrl] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [searchError, setSearchError] = useState<ApiError | Error | null>(null);

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
                  {/* Search artwork is the one image that is still a publisher URL: at this
                      point the show is not subscribed, so there is nothing for the server
                      to have cached yet. */}
                  <Artwork
                    className="episode-art"
                    src={result.image_url}
                    alt=""
                    fallbackText={result.title}
                  />
                  <div className="episode-body">
                    <div className="episode-title" style={{ cursor: "default" }}>
                      {result.title ?? result.feed_url}
                    </div>
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
                  <div className="episode-actions">
                    {result.already_subscribed ? (
                      <span className="tag" style={{ alignSelf: "center" }}>Subscribed</span>
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
    </>
  );
}
