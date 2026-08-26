export function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return "";
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes} min`;
  return `${total}s`;
}

/** Clock format for the player's own readout, where every second matters. */
export function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const padded = `${minutes.toString().padStart(hours > 0 ? 2 : 1, "0")}:${secs
    .toString()
    .padStart(2, "0")}`;
  return hours > 0 ? `${hours}:${padded}` : padded;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";

  const elapsedDays = (Date.now() - date.getTime()) / 86_400_000;
  if (elapsedDays < 1) {
    const hours = Math.floor((Date.now() - date.getTime()) / 3_600_000);
    if (hours < 1) return "just now";
    return `${hours}h ago`;
  }
  if (elapsedDays < 7) return `${Math.floor(elapsedDays)}d ago`;

  return date.toLocaleDateString(undefined, {
    year: elapsedDays > 300 ? "numeric" : undefined,
    month: "short",
    day: "numeric",
  });
}

export function formatRelativeExact(iso: string | null | undefined): string {
  if (!iso) return "never";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "never";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
