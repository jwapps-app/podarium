/** Mirrors podarium/schemas.py. Note what is absent: no enclosure_url, no publisher
 *  image_url. The API deliberately does not expose them, so the UI cannot leak them. */

export type RetentionMode = "after_played" | "after_download" | "never";

export interface User {
  id: number;
  username: string;
  created_at: string;
}

export interface ApiTokenSummary {
  id: number;
  name: string;
  created_at: string;
  last_used_at: string | null;
}

export interface CreatedApiToken extends ApiTokenSummary {
  /** Returned exactly once, at creation. */
  token: string;
}

export interface Feed {
  id: number;
  feed_url: string;
  podcast_index_id: number | null;
  title: string | null;
  author: string | null;
  description: string | null;
  link: string | null;
  language: string | null;
  explicit: boolean;
  /** Always an /api/images path, never a publisher CDN. */
  image_url: string | null;
  /** null means the feed inherits the global default. */
  auto_download_count: number | null;
  /** What is actually applied, resolved server-side. */
  effective_auto_download_count: number;
  retention_mode: RetentionMode | null;
  retention_days: number | null;
  active: boolean;
  last_fetched_at: string | null;
  fetch_error: string | null;
  fetch_error_count: number;
  created_at: string;
  updated_at: string;
  episode_count: number | null;
  unplayed_count: number | null;
  /** Episodes that arrived since this show was last looked at. This drives the badge. */
  new_episode_count: number | null;
}

export interface Episode {
  id: number;
  feed_id: number;
  guid: string;
  title: string | null;
  description_html: string | null;
  image_url: string | null;
  episode_number: number | null;
  season: number | null;
  explicit: boolean;
  /** For display only. first_seen_at is what "is this new?" is built on. */
  published_at: string | null;
  first_seen_at: string;
  duration_seconds: number | null;
  enclosure_type: string | null;
  enclosure_bytes: number | null;
  downloaded: boolean;
  local_bytes: number | null;
  downloaded_at: string | null;
  purged_at: string | null;
  /** Always an /api/stream path. */
  stream_url: string;
  played: boolean;
  position_seconds: number;
  completed_at: string | null;
  starred: boolean;
  updated_at: string;
}

export interface EpisodeList {
  items: Episode[];
  next_cursor: string | null;
}

export interface QueueItem {
  episode_id: number;
  position: number;
  added_at: string;
  episode: Episode;
}

export interface SearchResult {
  podcast_index_id: number | null;
  title: string | null;
  author: string | null;
  description: string | null;
  feed_url: string;
  image_url: string | null;
  episode_count: number | null;
  already_subscribed: boolean;
}

export interface AppSettings {
  global_retention_mode: RetentionMode;
  global_retention_days: number;
  download_dir_max_bytes: number | null;
  refresh_interval_minutes: number;
  user_agent: string;
  /** Starting speed for every episode. Server-side so iOS starts where the web player does. */
  default_playback_rate: number;
  /** Default number of newest episodes to pre-download, for feeds that do not override it. */
  global_auto_download_count: number;
}

export interface OpmlImportResult {
  imported: number;
  skipped: number;
  failed: number;
  errors: string[];
}

export interface EpisodeFilters {
  feed_id?: number;
  unplayed?: boolean;
  downloaded?: boolean;
  starred?: boolean;
  since?: string;
  limit?: number;
  cursor?: string;
}
