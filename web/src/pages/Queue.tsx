import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Artwork } from "../components/Artwork";
import { GripIcon, PauseIcon, PlayIcon, TrashIcon } from "../components/Icons";
import { Empty, ErrorNotice, Loading } from "../components/Loading";
import { api } from "../lib/api";
import { formatDuration } from "../lib/format";
import { usePlayer } from "../lib/player";
import { useEpisodeActions, useFeeds, useQueue } from "../lib/queries";
import { useDragReorder } from "../lib/useDragReorder";
import type { QueueItem } from "../lib/types";

export function QueuePage() {
  const { data: queue, isLoading, error } = useQueue();
  const { data: feeds } = useFeeds();
  const player = usePlayer();
  const actions = useEpisodeActions();
  const queryClient = useQueryClient();

  // Local mirror so a drag reorders instantly instead of waiting on the round trip.
  const [items, setItems] = useState<QueueItem[]>([]);

  useEffect(() => {
    if (queue) setItems(queue);
  }, [queue]);

  const reorder = useMutation({
    mutationFn: (episodeIds: number[]) => api.reorderQueue(episodeIds),
    onSuccess: (updated) => {
      setItems(updated);
      queryClient.invalidateQueries({ queryKey: ["queue"] });
    },
    onError: () => {
      // Snap back to the server's truth rather than leaving a lie on screen.
      queryClient.invalidateQueries({ queryKey: ["queue"] });
    },
  });

  const commit = (next: QueueItem[]) => {
    setItems(next);
    reorder.mutate(next.map((item) => item.episode_id));
  };

  const move = (episodeId: number, delta: number) => {
    const index = items.findIndex((item) => item.episode_id === episodeId);
    const target = index + delta;
    if (index === -1 || target < 0 || target >= items.length) return;
    const next = [...items];
    const [moved] = next.splice(index, 1);
    next.splice(target, 0, moved);
    commit(next);
  };

  const { draggingKey, registerRow, handleProps } = useDragReorder<QueueItem>(
    items,
    (item) => item.episode_id,
    setItems,
    commit,
  );

  if (isLoading) return <Loading label="Loading queue" />;
  if (error) return <ErrorNotice error={error} />;

  const feedTitles = new Map((feeds ?? []).map((feed) => [feed.id, feed.title]));

  return (
    <>
      <header className="page-head">
        <div>
          <h1 className="page-title">Queue</h1>
          <p className="page-subtitle">
            Queued episodes download automatically and are never deleted by retention.
          </p>
        </div>
        {items.length > 0 ? (
          <button className="btn" onClick={() => player.play(items[0].episode)}>
            Play from top
          </button>
        ) : null}
      </header>

      {items.length === 0 ? (
        <Empty title="The queue is empty">
          <p>Add an episode from your inbox or a show page to line it up here.</p>
        </Empty>
      ) : (
        <div>
          {items.map((item, index) => {
            const isCurrent = player.episode?.id === item.episode_id;
            const isPlaying = isCurrent && player.playing;
            return (
              <div
                key={item.episode_id}
                ref={(element) => registerRow(item.episode_id, element)}
                className={[
                  "queue-item",
                  draggingKey === item.episode_id ? "dragging" : "",
                ].join(" ").trim()}
              >
                <span
                  className="drag-handle"
                  title="Drag to reorder"
                  aria-label={`Reorder ${item.episode.title ?? "episode"}`}
                  {...handleProps(item.episode_id)}
                >
                  <GripIcon style={{ width: 16, height: 16 }} />
                </span>
                <span className="queue-position">{index + 1}</span>

                <Artwork
                  className="episode-art"
                  src={item.episode.image_url}
                  alt=""
                  fallbackText={feedTitles.get(item.episode.feed_id) ?? item.episode.title}
                />

                <div className="episode-body">
                  <div className="episode-show">{feedTitles.get(item.episode.feed_id) ?? ""}</div>
                  <div className="episode-title" style={{ cursor: "default" }}>
                    {item.episode.title ?? "Untitled episode"}
                  </div>
                  <div className="episode-meta">
                    {item.episode.downloaded ? (
                      <span className="tag tag-downloaded">downloaded</span>
                    ) : (
                      <span className="tag">queued for download</span>
                    )}
                    {item.episode.duration_seconds ? (
                      <>
                        <span className="dot">·</span>
                        <span>{formatDuration(item.episode.duration_seconds)}</span>
                      </>
                    ) : null}
                  </div>
                </div>

                <div className="episode-actions">
                  {/* Keyboard-reachable equivalent of dragging. */}
                  <button
                    className="btn-icon"
                    onClick={() => move(item.episode_id, -1)}
                    disabled={index === 0}
                    aria-label="Move up"
                    title="Move up"
                  >
                    ↑
                  </button>
                  <button
                    className="btn-icon"
                    onClick={() => move(item.episode_id, 1)}
                    disabled={index === items.length - 1}
                    aria-label="Move down"
                    title="Move down"
                  >
                    ↓
                  </button>
                  <button
                    className="btn-icon"
                    onClick={() => (isCurrent ? player.toggle() : player.play(item.episode))}
                    aria-label={isPlaying ? "Pause" : "Play"}
                  >
                    {isPlaying ? <PauseIcon /> : <PlayIcon />}
                  </button>
                  <button
                    className="btn-icon"
                    onClick={() => actions.dequeue.mutate(item.episode_id)}
                    aria-label="Remove from queue"
                    title="Remove from queue"
                  >
                    <TrashIcon />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
