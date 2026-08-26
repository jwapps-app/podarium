/** Inline icons. Bundled rather than linked so the UI needs no external requests at all. */

type Props = { className?: string; style?: React.CSSProperties };

const base = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export const LibraryIcon = (p: Props) => (
  <svg {...base} {...p}><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></svg>
);

export const InboxIcon = (p: Props) => (
  <svg {...base} {...p}><path d="M4 13h4l2 3h4l2-3h4" /><path d="M5.5 5h13l2.5 8v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4z" /></svg>
);

export const QueueIcon = (p: Props) => (
  <svg {...base} {...p}><path d="M4 6h11M4 12h11M4 18h7" /><path d="M17 12.5v6.2" /><circle cx="19" cy="19" r="2" /></svg>
);

export const SearchIcon = (p: Props) => (
  <svg {...base} {...p}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.4-3.4" /></svg>
);

export const SettingsIcon = (p: Props) => (
  <svg {...base} {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2v.2a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.9H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.6 8a1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V2a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H22a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></svg>
);

export const PlayIcon = (p: Props) => (
  <svg viewBox="0 0 24 24" fill="currentColor" {...p}><path d="M8 5.2c0-.9 1-1.5 1.8-1L19 11a1.2 1.2 0 0 1 0 2l-9.2 6.8c-.8.5-1.8-.1-1.8-1z" /></svg>
);

export const PauseIcon = (p: Props) => (
  <svg viewBox="0 0 24 24" fill="currentColor" {...p}><rect x="6" y="5" width="4" height="14" rx="1.2" /><rect x="14" y="5" width="4" height="14" rx="1.2" /></svg>
);

export const Back15Icon = (p: Props) => (
  <svg {...base} {...p}><path d="M11 4 7 8l4 4" /><path d="M7 8h6a6 6 0 1 1-6 6" /></svg>
);

export const Forward30Icon = (p: Props) => (
  <svg {...base} {...p}><path d="m13 4 4 4-4 4" /><path d="M17 8h-6a6 6 0 1 0 6 6" /></svg>
);

export const DownloadIcon = (p: Props) => (
  <svg {...base} {...p}><path d="M12 3v12" /><path d="m7.5 10.5 4.5 4.5 4.5-4.5" /><path d="M4 20h16" /></svg>
);

export const CheckIcon = (p: Props) => (
  <svg {...base} {...p}><path d="m5 12.5 4.5 4.5L19 7" /></svg>
);

export const TrashIcon = (p: Props) => (
  <svg {...base} {...p}><path d="M4 7h16" /><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" /><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12" /></svg>
);

export const PlusIcon = (p: Props) => (
  <svg {...base} {...p}><path d="M12 5v14M5 12h14" /></svg>
);

export const StarIcon = ({ filled, ...p }: Props & { filled?: boolean }) => (
  <svg {...base} fill={filled ? "currentColor" : "none"} {...p}><path d="m12 3.6 2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.5 9.8l5.9-.9z" /></svg>
);

export const RefreshIcon = (p: Props) => (
  <svg {...base} {...p}><path d="M20 11a8 8 0 1 0-.6 4" /><path d="M20 4v7h-7" /></svg>
);

export const GripIcon = (p: Props) => (
  <svg viewBox="0 0 24 24" fill="currentColor" {...p}><circle cx="9" cy="6" r="1.5" /><circle cx="15" cy="6" r="1.5" /><circle cx="9" cy="12" r="1.5" /><circle cx="15" cy="12" r="1.5" /><circle cx="9" cy="18" r="1.5" /><circle cx="15" cy="18" r="1.5" /></svg>
);

export const LogoutIcon = (p: Props) => (
  <svg {...base} {...p}><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="m16 17 5-5-5-5" /><path d="M21 12H9" /></svg>
);

export const BrandMark = (p: Props) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" {...p}>
    <path d="M12 14.5a3 3 0 0 0 3-3v-4a3 3 0 0 0-6 0v4a3 3 0 0 0 3 3z" />
    <path d="M6.5 11.5a5.5 5.5 0 0 0 11 0" />
    <path d="M12 17v4" />
    <path d="M8.5 21h7" />
  </svg>
);
