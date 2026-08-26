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
  TotpSetup,
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

  /** Too many failed sign-ins. Distinct from a wrong password, and worth saying so. */
  get isRateLimited(): boolean {
    return this.status === 429;
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

  let payload: { error?: { code?: string; message?: string } } | undefined;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      // Not JSON, so something other than Podarium answered -- a proxy error page, a
      // captive portal, a challenge. Parsing it produced a raw
      // "Unexpected token '<'" that named neither the request nor the status, which is
      // the least useful thing to show for a failure that is usually not even ours.
      throw new ApiError(
        response.status,
        `non_json_${response.status}`,
        `${path} returned ${response.status} ${response.statusText || ""}`.trim() +
          ` with ${describeBody(text)} instead of JSON.` +
          (response.ok
            ? " Something between the browser and Podarium answered instead of the API."
            : ""),
      );
    }
  }

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

/** A short human description of an unexpected body, for the error message. */
function describeBody(text: string): string {
  const head = text.trimStart().slice(0, 200).toLowerCase();
  if (head.startsWith("<!doctype html") || head.startsWith("<html")) return "an HTML page";
  if (head.startsWith("<")) return "an XML or HTML document";
  return `${text.length} bytes of non-JSON`;
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

  login: (username: string, password: string, totpCode?: string) =>
    request<User>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password, totp_code: totpCode || undefined }),
    }),

  totpSetup: () => request<TotpSetup>("/api/auth/totp/setup", { method: "POST" }),

  totpEnable: (secret: string, code: string) =>
    request<User>(`/api/auth/totp/enable?secret=${encodeURIComponent(secret)}`, {
      method: "POST",
      body: JSON.stringify({ code }),
    }),

  totpDisable: (password: string) =>
    request<User>("/api/auth/totp/disable", {
      method: "POST",
      body: JSON.stringify({ password }),
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

  markFeedSeen: (id: number) => request<Feed>(`/api/feeds/${id}/seen`, { method: "POST" }),

  markAllFeedsSeen: () => request<void>("/api/feeds/seen", { method: "POST" }),

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
  ) =>
    request<Episode>(`/api/episodes/${id}/state`, {
      method: "PUT",
      // Stamped with when the change was made, not when it arrives. The pagehide beacon
      // below is the browser's own version of an offline flush: it can land well after the
      // tab is gone, by which time another device may have moved on.
      body: JSON.stringify({ ...body, changed_at: new Date().toISOString() }),
    }),

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
