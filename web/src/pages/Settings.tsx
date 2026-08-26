import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ErrorNotice, Loading } from "../components/Loading";
import { TrashIcon } from "../components/Icons";
import { api } from "../lib/api";
import { formatBytes, formatRelativeExact } from "../lib/format";
import { PLAYBACK_RATES } from "../lib/player";
import { useSettings } from "../lib/queries";
import type { CreatedApiToken, OpmlImportResult, RetentionMode } from "../lib/types";

export function SettingsPage() {
  const { data: settings, isLoading, error } = useSettings();

  if (isLoading) return <Loading label="Loading settings" />;
  if (error) return <ErrorNotice error={error} />;
  if (!settings) return null;

  return (
    <>
      <header className="page-head">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-subtitle">Defaults for every show, plus import and devices.</p>
        </div>
      </header>

      <GlobalSettings
        initial={{
          global_retention_mode: settings.global_retention_mode,
          global_retention_days: settings.global_retention_days,
          download_dir_max_bytes: settings.download_dir_max_bytes,
          refresh_interval_minutes: settings.refresh_interval_minutes,
          user_agent: settings.user_agent,
          default_playback_rate: settings.default_playback_rate,
          global_auto_download_count: settings.global_auto_download_count,
        }}
      />
      <OpmlPanel />
      <TokensPanel />
    </>
  );
}

interface GlobalValues {
  global_retention_mode: RetentionMode;
  global_retention_days: number;
  download_dir_max_bytes: number | null;
  refresh_interval_minutes: number;
  user_agent: string;
  default_playback_rate: number;
  global_auto_download_count: number;
}

function GlobalSettings({ initial }: { initial: GlobalValues }) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState(initial.global_retention_mode);
  const [days, setDays] = useState(String(initial.global_retention_days));
  const [interval, setInterval] = useState(String(initial.refresh_interval_minutes));
  const [userAgent, setUserAgent] = useState(initial.user_agent);
  const [rate, setRate] = useState(String(initial.default_playback_rate));
  const [autoDownload, setAutoDownload] = useState(String(initial.global_auto_download_count));
  // Exposed in GB because a byte ceiling is unreadable at NAS scale.
  const [ceilingGb, setCeilingGb] = useState(
    initial.download_dir_max_bytes === null
      ? ""
      : String(Math.round((initial.download_dir_max_bytes / 1024 ** 3) * 10) / 10),
  );

  const save = useMutation({
    mutationFn: (body: Parameters<typeof api.updateSettings>[0]) => api.updateSettings(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = ceilingGb.trim();
    save.mutate({
      global_retention_mode: mode,
      global_retention_days: Number(days) || 0,
      refresh_interval_minutes: Math.max(1, Number(interval) || 60),
      user_agent: userAgent.trim() || initial.user_agent,
      default_playback_rate: Number(rate) || 1,
      global_auto_download_count: Math.max(0, Number(autoDownload) || 0),
      ...(trimmed === ""
        ? { clear_download_dir_max_bytes: true }
        : { download_dir_max_bytes: Math.round(Number(trimmed) * 1024 ** 3) }),
    });
  };

  return (
    <form className="panel" onSubmit={submit}>
      <div className="panel-title">Defaults</div>
      <p className="panel-hint">
        Any show can override retention on its own page. These apply everywhere else.
      </p>

      <div className="field-row">
        <div className="field">
          <label htmlFor="global-mode">Retention</label>
          <select
            id="global-mode"
            value={mode}
            onChange={(event) => setMode(event.target.value as RetentionMode)}
          >
            <option value="after_played">Delete after played</option>
            <option value="after_download">Delete after download</option>
            <option value="never">Keep forever</option>
          </select>
        </div>

        <div className="field">
          <label htmlFor="global-days">Keep for (days)</label>
          <input
            id="global-days"
            type="number"
            min={0}
            value={days}
            onChange={(event) => setDays(event.target.value)}
          />
        </div>

        <div className="field">
          <label htmlFor="interval">Refresh every (minutes)</label>
          <input
            id="interval"
            type="number"
            min={1}
            value={interval}
            onChange={(event) => setInterval(event.target.value)}
          />
        </div>
      </div>

      <div className="field">
        <label htmlFor="global-auto">Auto-download newest</label>
        <input
          id="global-auto"
          type="number"
          min={0}
          value={autoDownload}
          onChange={(event) => setAutoDownload(event.target.value)}
        />
        <div className="field-hint">
          How many recent episodes to keep on disk for every show that does not set its
          own. 0 downloads nothing until you queue an episode. Raising this starts
          downloading straight away, so mind the disk on shows with long episodes.
        </div>
      </div>

      <div className="field">
        <label htmlFor="ceiling">Download directory ceiling (GB)</label>
        <input
          id="ceiling"
          type="number"
          min={0}
          step={0.5}
          placeholder="No limit"
          value={ceilingGb}
          onChange={(event) => setCeilingGb(event.target.value)}
        />
        <div className="field-hint">
          When the directory exceeds this, played episodes are removed first, oldest
          downloads next. Queued episodes are never touched.
          {initial.download_dir_max_bytes
            ? ` Currently ${formatBytes(initial.download_dir_max_bytes)}.`
            : ""}
        </div>
      </div>

      <div className="field">
        <label htmlFor="rate">Default playback speed</label>
        <select id="rate" value={rate} onChange={(event) => setRate(event.target.value)}>
          {PLAYBACK_RATES.map((value) => (
            <option key={value} value={value}>
              {value}&times;{value === 1 ? " (normal)" : ""}
            </option>
          ))}
        </select>
        <div className="field-hint">
          Every episode starts at this speed. The player&rsquo;s speed button still changes
          it for the current session without touching this default.
        </div>
      </div>

      <div className="field">
        <label htmlFor="ua">User agent</label>
        <input id="ua" value={userAgent} onChange={(event) => setUserAgent(event.target.value)} />
        <div className="field-hint">Sent on every outbound request to a publisher.</div>
      </div>

      <button className="btn btn-primary" type="submit" disabled={save.isPending}>
        {save.isPending ? "Saving…" : "Save defaults"}
      </button>
      {save.isSuccess ? (
        <span style={{ marginLeft: 12, color: "var(--success)", fontSize: 13.5 }}>Saved</span>
      ) : null}
      {save.error ? <div className="notice notice-error" style={{ marginTop: 12 }}>{(save.error as Error).message}</div> : null}
    </form>
  );
}

function OpmlPanel() {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [result, setResult] = useState<OpmlImportResult | null>(null);

  const importOpml = useMutation({
    mutationFn: (xml: string) => api.importOpml(xml),
    onSuccess: (data) => {
      setResult(data);
      queryClient.invalidateQueries({ queryKey: ["feeds"] });
    },
  });

  const onFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    importOpml.mutate(await file.text());
    event.target.value = "";
  };

  return (
    <div className="panel">
      <div className="panel-title">Subscriptions</div>
      <p className="panel-hint">
        Import an OPML file from another app, or export yours. Imported feeds pick up their
        titles and episodes on the next refresh.
      </p>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <a className="btn" href={api.opmlExportUrl} download="podarium.opml">
          Export OPML
        </a>
        <button className="btn" onClick={() => fileRef.current?.click()} disabled={importOpml.isPending}>
          {importOpml.isPending ? "Importing…" : "Import OPML"}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".opml,.xml,text/xml,application/xml"
          onChange={onFile}
          style={{ display: "none" }}
        />
      </div>

      {result ? (
        <div className="notice" style={{ marginTop: 14 }}>
          Imported {result.imported}, already subscribed {result.skipped}, failed {result.failed}.
          {result.errors.length > 0 ? (
            <ul className="mono" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              {result.errors.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {importOpml.error ? (
        <div className="notice notice-error" style={{ marginTop: 14 }}>
          {(importOpml.error as Error).message}
        </div>
      ) : null}
    </div>
  );
}

function TokensPanel() {
  const queryClient = useQueryClient();
  const { data: tokens } = useQuery({ queryKey: ["tokens"], queryFn: api.listTokens });
  const [name, setName] = useState("");
  const [created, setCreated] = useState<CreatedApiToken | null>(null);

  // The plaintext is shown once and never returned again; clear it when leaving the page.
  useEffect(() => () => setCreated(null), []);

  const create = useMutation({
    mutationFn: (value: string) => api.createToken(value),
    onSuccess: (token) => {
      setCreated(token);
      setName("");
      queryClient.invalidateQueries({ queryKey: ["tokens"] });
    },
  });

  const revoke = useMutation({
    mutationFn: (id: number) => api.revokeToken(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tokens"] }),
  });

  return (
    <div className="panel">
      <div className="panel-title">Device tokens</div>
      <p className="panel-hint">
        Long-lived tokens for non-browser clients. Revoking one signs out just that device.
      </p>

      <form
        style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim()) create.mutate(name.trim());
        }}
      >
        <div className="field" style={{ marginBottom: 0, flex: 1, minWidth: 200 }}>
          <label htmlFor="token-name">Device name</label>
          <input
            id="token-name"
            value={name}
            placeholder="iPhone"
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <button className="btn" type="submit" disabled={create.isPending || !name.trim()}>
          Create token
        </button>
      </form>

      {created ? (
        <div className="token-reveal">
          <strong>Copy this now — it is not shown again.</strong>
          <div className="mono" style={{ marginTop: 6 }}>{created.token}</div>
        </div>
      ) : null}

      {tokens && tokens.length > 0 ? (
        <table className="table" style={{ marginTop: 16 }}>
          <thead>
            <tr>
              <th>Device</th>
              <th>Created</th>
              <th>Last used</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {tokens.map((token) => (
              <tr key={token.id}>
                <td>{token.name || <span style={{ color: "var(--text-faint)" }}>unnamed</span>}</td>
                <td>{formatRelativeExact(token.created_at)}</td>
                <td>{token.last_used_at ? formatRelativeExact(token.last_used_at) : "never"}</td>
                <td style={{ textAlign: "right" }}>
                  <button
                    className="btn-icon"
                    onClick={() => revoke.mutate(token.id)}
                    aria-label={`Revoke ${token.name || "token"}`}
                    title="Revoke"
                  >
                    <TrashIcon />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}
