import { Link } from "react-router-dom";

import { Artwork } from "../components/Artwork";
import { Empty, ErrorNotice, Loading } from "../components/Loading";
import { useFeeds } from "../lib/queries";

export function LibraryPage() {
  const { data: feeds, isLoading, error } = useFeeds();

  if (isLoading) return <Loading label="Loading your shows" />;
  if (error) return <ErrorNotice error={error} />;

  const active = (feeds ?? []).filter((feed) => feed.active);
  const inactive = (feeds ?? []).filter((feed) => !feed.active);

  return (
    <>
      <header className="page-head">
        <div>
          <h1 className="page-title">Library</h1>
          <p className="page-subtitle">
            {active.length} {active.length === 1 ? "show" : "shows"}
          </p>
        </div>
        <Link className="btn btn-primary" to="/search">Add a show</Link>
      </header>

      {active.length === 0 ? (
        <Empty title="No subscriptions yet">
          <p>Find a show by name, or paste a feed URL directly.</p>
          <Link className="btn btn-primary" to="/search" style={{ marginTop: 12 }}>
            Search for a podcast
          </Link>
        </Empty>
      ) : (
        <div className="feed-grid">
          {active.map((feed) => (
            <Link key={feed.id} className="feed-card" to={`/feeds/${feed.id}`}>
              <Artwork
                className="feed-art"
                src={feed.image_url}
                alt={feed.title ?? feed.feed_url}
                fallbackText={feed.title}
              />
              {feed.fetch_error_count > 0 ? (
                <span className="badge badge-error" title={feed.fetch_error ?? "Refresh failed"}>!</span>
              ) : feed.new_episode_count ? (
                <span
                  className="badge"
                  title={`${feed.new_episode_count} new since you last opened this show`}
                >
                  {feed.new_episode_count > 99 ? "99+" : feed.new_episode_count}
                </span>
              ) : null}
              <div className="feed-card-title">{feed.title ?? feed.feed_url}</div>
              {feed.author ? <div className="feed-card-author">{feed.author}</div> : null}
            </Link>
          ))}
        </div>
      )}

      {inactive.length > 0 ? (
        <section style={{ marginTop: 34 }}>
          <h2 className="panel-title" style={{ marginBottom: 12 }}>Unsubscribed</h2>
          <p className="panel-hint">
            Hidden from the library, but their episodes and played state are still here.
          </p>
          <div className="feed-grid">
            {inactive.map((feed) => (
              <Link key={feed.id} className="feed-card" to={`/feeds/${feed.id}`} style={{ opacity: 0.55 }}>
                <Artwork className="feed-art" src={feed.image_url} alt={feed.title ?? feed.feed_url} fallbackText={feed.title} />
                <div className="feed-card-title">{feed.title ?? feed.feed_url}</div>
              </Link>
            ))}
          </div>
        </section>
      ) : null}
    </>
  );
}
