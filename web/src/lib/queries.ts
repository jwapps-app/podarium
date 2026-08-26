import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./api";
import type { EpisodeFilters } from "./types";

/** Invalidated together because almost every action touches more than one of them:
 *  downloading changes an episode and the queue, playing changes an episode and its
 *  feed's unplayed count. */
function invalidateAll(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ["episodes"] });
  queryClient.invalidateQueries({ queryKey: ["episode"] });
  queryClient.invalidateQueries({ queryKey: ["queue"] });
  queryClient.invalidateQueries({ queryKey: ["feeds"] });
  queryClient.invalidateQueries({ queryKey: ["feed"] });
}

export function useFeeds() {
  return useQuery({ queryKey: ["feeds"], queryFn: api.feeds });
}

export function useFeed(id: number) {
  return useQuery({ queryKey: ["feed", id], queryFn: () => api.feed(id), enabled: Number.isFinite(id) });
}

export function useEpisodes(filters: EpisodeFilters) {
  return useQuery({
    queryKey: ["episodes", filters],
    queryFn: () => api.episodes(filters),
  });
}

export function useQueue() {
  return useQuery({ queryKey: ["queue"], queryFn: api.queue });
}

export function useSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: api.settings });
}

/** Every episode-level action, sharing one invalidation policy. */
export function useEpisodeActions() {
  const queryClient = useQueryClient();
  const onSettled = () => invalidateAll(queryClient);

  return {
    setState: useMutation({
      mutationFn: ({
        id,
        ...body
      }: { id: number; played?: boolean; position_seconds?: number; starred?: boolean }) =>
        api.setState(id, body),
      onSettled,
    }),
    download: useMutation({ mutationFn: (id: number) => api.download(id), onSettled }),
    removeDownload: useMutation({ mutationFn: (id: number) => api.removeDownload(id), onSettled }),
    enqueue: useMutation({ mutationFn: (id: number) => api.enqueue(id), onSettled }),
    dequeue: useMutation({ mutationFn: (id: number) => api.dequeue(id), onSettled }),
  };
}

export function useFeedActions() {
  const queryClient = useQueryClient();
  const onSettled = () => invalidateAll(queryClient);

  return {
    refresh: useMutation({ mutationFn: (id: number) => api.refreshFeed(id), onSettled }),
    markSeen: useMutation({ mutationFn: (id: number) => api.markFeedSeen(id), onSettled }),
    update: useMutation({
      mutationFn: ({ id, ...body }: { id: number } & Parameters<typeof api.updateFeed>[1]) =>
        api.updateFeed(id, body),
      onSettled,
    }),
    unsubscribe: useMutation({
      mutationFn: ({ id, purge }: { id: number; purge: boolean }) => api.unsubscribe(id, purge),
      onSettled,
    }),
    subscribe: useMutation({
      mutationFn: (body: { feed_url?: string; podcast_index_id?: number }) => api.subscribe(body),
      onSettled,
    }),
  };
}
