import type {
  ApiTokenSummary,
  AppSettings,
  CreatedApiToken,
  Episode,
  EpisodeFilters,
  EpisodeList,
  Feed,
  OpmlImportResult,
  QueueItem,
  RetentionMode,
  SearchResult,
  User,
} from "./types";

/** Thrown for any non-2xx response, carrying the API's {"error": {code, message}} body. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  /** Podcast Index credentials are not configured. Expected, not a failure. */
  get isServiceUnavailable(): boolean {
    return this.status === 503;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    // The session cookie is httpOnly; it rides along because this is same-origin.
    credentials: "same-origin",
    ...init,
    headers: {
      ...(init.body && !(init.body instanceof FormData)
        ? { "content-type": "application/json" }
        : {}),
      ...init.headers,
    },
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  const payload = text ? JSON.parse(text) : undefined;

  if (!response.ok) {
    const error = payload?.error;
    throw new ApiError(
      response.status,
      error?.code ?? `http_${response.status}`,
      error?.message ?? response.statusText,
    );
  }

  return payload as T;
}

function query(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : "";
}

export const api = {
  // -- auth ---------------------------------------------------------------
  me: () => request<User>("/api/auth/me"),

  login: (username: string, password: string) =>
    request<User>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  logout: () => request<void>("/api/auth/logout", { method: "POST" }),

  listTokens: () => request<ApiTokenSummary[]>("/api/auth/token"),

  createToken: (name: string) =>
    request<CreatedApiToken>("/api/auth/token", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  revokeToken: (id: number) => request<void>(`/api/auth/token/${id}`, { method: "DELETE" }),

  // -- search and subscribe -----------------------------------------------
  search: (q: string) => request<SearchResult[]>(`/api/search${query({ q })}`),

  resolveFeedUrl: (url: string) =>
    request<SearchResult>(`/api/search/byfeedurl${query({ url })}`),

  subscribe: (body: { feed_url?: string; podcast_index_id?: number }) =>
    request<Feed>("/api/feeds", { method: "POST", body: JSON.stringify(body) }),

  // -- feeds ---------------------------------------------------------------
  feeds: () => request<Feed[]>("/api/feeds"),

  feed: (id: number) => request<Feed>(`/api/feeds/${id}`),

  updateFeed: (
    id: number,
    body: {
      auto_download_count?: number;
      retention_mode?: RetentionMode;
      retention_days?: number;
      active?: boolean;
      clear_retention_mode?: boolean;
      clear_retention_days?: boolean;
      clear_auto_download_count?: boolean;
    },
  ) => request<Feed>(`/api/feeds/${id}`, { method: "PATCH", body: JSON.stringify(body) }),

  unsubscribe: (id: number, purge: boolean) =>
    request<void>(`/api/feeds/${id}${query({ purge })}`, { method: "DELETE" }),

  refreshFeed: (id: number) => request<Feed>(`/api/feeds/${id}/refresh`, { method: "POST" }),

  // -- episodes ------------------------------------------------------------
  episodes: (filters: EpisodeFilters = {}) =>
    request<EpisodeList>(`/api/episodes${query({ ...filters })}`),

  episode: (id: number) => request<Episode>(`/api/episodes/${id}`),

  download: (id: number) =>
    request<Episode>(`/api/episodes/${id}/download`, { method: "POST" }),

  removeDownload: (id: number) =>
    request<void>(`/api/episodes/${id}/download`, { method: "DELETE" }),

  setState: (
    id: number,
    body: { played?: boolean; position_seconds?: number; starred?: boolean },
  ) => request<Episode>(`/api/episodes/${id}/state`, { method: "PUT", body: JSON.stringify(body) }),

  // -- queue ----------------------------------------------------------------
  queue: () => request<QueueItem[]>("/api/queue"),

  enqueue: (episode_id: number, position?: number) =>
    request<QueueItem[]>("/api/queue", {
      method: "POST",
      body: JSON.stringify({ episode_id, position }),
    }),

  dequeue: (episode_id: number) =>
    request<void>(`/api/queue/${episode_id}`, { method: "DELETE" }),

  reorderQueue: (episode_ids: number[]) =>
    request<QueueItem[]>("/api/queue/order", {
      method: "PUT",
      body: JSON.stringify({ episode_ids }),
    }),

  // -- settings and admin -----------------------------------------------------
  settings: () => request<AppSettings>("/api/settings"),

  updateSettings: (body: Partial<AppSettings> & { clear_download_dir_max_bytes?: boolean }) =>
    request<AppSettings>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),

  importOpml: (xml: string) =>
    request<OpmlImportResult>("/api/opml/import", {
      method: "POST",
      headers: { "content-type": "text/xml" },
      body: xml,
    }),

  opmlExportUrl: "/api/opml/export",
};
