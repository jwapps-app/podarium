"""Prometheus metrics. The fleet already scrapes, so /metrics is part of phase 1."""

from prometheus_client import Counter, Gauge

feed_refresh_total = Counter(
    "podarium_feed_refresh_total", "Feed refresh attempts", ["result"]
)  # result: success | not_modified | error

episodes_discovered_total = Counter(
    "podarium_episodes_discovered_total", "Episodes seen for the first time"
)

download_total = Counter(
    "podarium_download_total", "Download job outcomes", ["result"]
)  # result: done | failed | skipped

downloaded_bytes_total = Counter(
    "podarium_downloaded_bytes_total", "Bytes written to the download directory"
)

purged_total = Counter(
    "podarium_purged_total", "Episode files removed by retention", ["reason"]
)  # reason: policy | ceiling | manual | window

download_queue_depth = Gauge(
    "podarium_download_queue_depth", "Download jobs in queued or running state"
)

download_dir_bytes = Gauge(
    "podarium_download_dir_bytes", "Bytes currently on disk according to the database"
)

feeds_with_errors = Gauge(
    "podarium_feeds_with_errors", "Feeds whose last refresh attempt failed"
)
