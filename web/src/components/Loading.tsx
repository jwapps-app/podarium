/** The foot of a paged list. Says so when there is no more, rather than just ending. */
export function LoadMore({
  hasMore,
  loading,
  onMore,
}: {
  hasMore: boolean;
  loading: boolean;
  onMore: () => void;
}) {
  if (!hasMore) return null;
  return (
    <div style={{ display: "flex", justifyContent: "center", padding: 12 }}>
      <button className="btn" disabled={loading} onClick={onMore}>
        {loading ? "Loading…" : "Show more"}
      </button>
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="center-fill">
      <span className="spinner" />
      {label}
    </div>
  );
}

export function ErrorNotice({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  return <div className="notice notice-error">{message}</div>;
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {children}
    </div>
  );
}
