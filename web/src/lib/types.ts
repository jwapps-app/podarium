/** Mirrors podarium/schemas.py. Note what is absent: no enclosure_url, no publisher
 *  image_url. The API deliberately does not expose them, so the UI cannot leak them. */

export type RetentionMode = "after_played" | "after_download" | "never";

export interface User {
  id: number;
  username: string;
  created_at: string;
  totp_enabled: boolean;
}

export interface TotpSetup {
  secret: string;
  provisioning_uri: string;
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
  /** null inherits the global default; effective_playback_rate is what plays. */
  playback_rate: number | null;
  effective_playback_rate: number;
  trim_silence: boolean | null;
  effective_trim_silence: boolean;
  normalize_audio: boolean | null;
  effective_normalize_audio: boolean;
  skip_sponsor_chapters: boolean | null;
  effective_skip_sponsor_chapters: boolean;
  intro_skip_seconds: number;
  outro_skip_seconds: number;
  /** Whether new episodes of this show are worth a notification. */
  notify: boolean;
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
  listened_seconds: number;
  last_played_at: string | null;
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

export interface Chapter {
  start_seconds: number;
  title: string | null;
  /** Looks like an ad break: the publisher hid it from the table of contents, or said so
   *  in the title. */
  sponsor: boolean;
}

export interface PushConfig {
  /** null when the server has no VAPID keys, so push cannot be offered. */
  public_key: string | null;
  subscribed: boolean;
}

export interface PreviewEpisode {
  guid: string;
  title: string | null;
  published_at: string | null;
  duration_seconds: number | null;
  description_html: string | null;
}

export interface Preview {
  title: string | null;
  author: string | null;
  description: string | null;
  feed_url: string;
  image_url: string | null;
  link: string | null;
  episode_count: number;
  already_subscribed: boolean;
  episodes: PreviewEpisode[];
}

export interface Bookmark {
  id: number;
  episode_id: number;
  position_seconds: number;
  note: string | null;
  created_at: string;
  episode_title: string | null;
  feed_id: number | null;
}

export interface ShowStats {
  feed_id: number;
  title: string | null;
  episodes_marked_played: number;
  episodes_listened: number;
  seconds_listened: number;
}

export interface Stats {
  episodes_marked_played: number;
  episodes_listened: number;
  seconds_listened: number;
  seconds_saved_by_speed: number;
  episodes_processed: number;
  in_progress: number;
  bookmarks: number;
  shows: ShowStats[];
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
  global_trim_silence: boolean;
  global_normalize_audio: boolean;
  global_skip_sponsor_chapters: boolean;
  /** False when the server has no ffmpeg, so the switches would do nothing. */
  audio_processing_available: boolean;
}

export interface FeedUsage {
  feed_id: number;
  title: string | null;
  bytes: number;
  episodes: number;
}

export interface Storage {
  total_bytes: number;
  episodes: number;
  /** Starred or queued: exempt from retention and from the ceiling. */
  protected_bytes: number;
  protected_episodes: number;
  /** What retention could take back if it had to. */
  reclaimable_bytes: number;
  /** The share that is trimmed or levelled copies kept beside their originals. */
  processed_bytes: number;
  ceiling_bytes: number | null;
  feeds: FeedUsage[];
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
  /** Started, not finished, ordered by when you last listened. */
  in_progress?: boolean;
  /** Free text over episode title, show title, and description. */
  q?: string;
  /** false omits description_html from list responses; rows fetch it on expand. */
  notes?: boolean;
  since?: string;
  limit?: number;
  cursor?: string;
}
