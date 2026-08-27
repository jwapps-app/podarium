import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ErrorNotice, Loading } from "../components/Loading";
import { TrashIcon } from "../components/Icons";
import { api } from "../lib/api";
import { describeSubscription, urlBase64ToUint8Array } from "../lib/offline";
import { useOffline } from "../lib/offlineStore";
import { formatBytes, formatRelativeExact } from "../lib/format";
import { PLAYBACK_RATES } from "../lib/player";
import { useAuth } from "../lib/auth";
import { useSettings, useStorage } from "../lib/queries";
import type { CreatedApiToken, OpmlImportResult, RetentionMode, TotpSetup } from "../lib/types";

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

      <StoragePanel />

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
      <NotificationsPanel />
      <OfflinePanel />
      <OpmlPanel />
      <TokensPanel />
      <AccountPanel />
    </>
  );
}

/** What is on disk, so the ceiling below can be set from measurement rather than a guess.
 *  Starred and queued episodes are called out separately because they are exempt from both
 *  retention and the ceiling -- they are the part of the library that grows unbounded. */
function StoragePanel() {
  const { data, isLoading, error } = useStorage();

  if (isLoading) return null;
  if (error) return <ErrorNotice error={error} />;
  if (!data) return null;

  const { total_bytes, ceiling_bytes, protected_bytes, reclaimable_bytes } = data;
  // Scaled to the ceiling when one is set, so the bar's empty tail reads as headroom.
  const scale = Math.max(total_bytes, ceiling_bytes ?? 0) || 1;
  const pct = (value: number) => `${(value / scale) * 100}%`;
  // formatBytes elides zero, which reads as a missing number in a legend.
  const size = (value: number) => formatBytes(value) || "0 B";

  return (
    <div className="panel">
      <div className="panel-title">On disk</div>
      <p className="panel-hint">
        {total_bytes === 0
          ? "Nothing downloaded yet."
          : `${formatBytes(total_bytes)} across ${data.episodes} ${
              data.episodes === 1 ? "episode" : "episodes"
            }.`}
        {ceiling_bytes ? ` Ceiling ${formatBytes(ceiling_bytes)}.` : " No ceiling set."}
      </p>

      {total_bytes > 0 ? (
        <>
          <div
            className="storage-bar"
            role="img"
            aria-label={`${size(protected_bytes)} protected, ${size(reclaimable_bytes)} reclaimable`}
          >
            <div className="storage-fill storage-fill-protected" style={{ width: pct(protected_bytes) }} />
            <div className="storage-fill storage-fill-reclaimable" style={{ width: pct(reclaimable_bytes) }} />
          </div>

          <div className="storage-legend">
            <span>
              <i className="storage-swatch storage-fill-protected" />
              {size(protected_bytes)} starred or queued
              {data.protected_episodes > 0 ? ` (${data.protected_episodes})` : ""}
            </span>
            <span>
              <i className="storage-swatch storage-fill-reclaimable" />
              {size(reclaimable_bytes)} retention can reclaim
            </span>
          </div>

          {protected_bytes > 0 ? (
            <div className="field-hint" style={{ marginTop: 10 }}>
              Starred and queued episodes are exempt from retention and from the ceiling. A
              ceiling below {formatBytes(protected_bytes)} could never be met.
            </div>
          ) : null}

          <table className="table" style={{ marginTop: 16 }}>
            <thead>
              <tr>
                <th>Show</th>
                <th className="storage-num">Episodes</th>
                <th className="storage-num">Size</th>
              </tr>
            </thead>
            <tbody>
              {data.feeds.map((feed) => (
                <tr key={feed.feed_id}>
                  <td>{feed.title || <span style={{ color: "var(--text-faint)" }}>untitled</span>}</td>
                  <td className="storage-num">{feed.episodes}</td>
                  <td className="storage-num">{formatBytes(feed.bytes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </div>
  );
}

function AccountPanel() {
  const { user, logout, refresh } = useAuth();
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const begin = async () => {
    setError(null);
    try {
      setSetup(await api.totpSetup());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const confirm = async () => {
    if (!setup) return;
    setError(null);
    try {
      await api.totpEnable(setup.secret, code);
      setSetup(null);
      setCode("");
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const turnOff = async () => {
    setError(null);
    try {
      await api.totpDisable(password);
      setPassword("");
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  return (
    <div className="panel">
      <div className="panel-title">Account</div>
      <p className="panel-hint">Signed in as {user?.username ?? "unknown"}.</p>

      <div className="field">
        <label>Two-factor authentication</label>
        {user?.totp_enabled ? (
          <>
            <div className="field-hint" style={{ marginBottom: 10 }}>
              On. A code from your authenticator is required to sign in.
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <input
                type="password"
                placeholder="Confirm your password"
                value={password}
                autoComplete="current-password"
                onChange={(event) => setPassword(event.target.value)}
                style={{ flex: 1, minWidth: 200 }}
              />
              <button className="btn btn-danger" disabled={!password} onClick={() => void turnOff()}>
                Turn off
              </button>
            </div>
          </>
        ) : setup ? (
          <>
            <div className="field-hint" style={{ marginBottom: 10 }}>
              Add this to your authenticator, then enter a code to confirm. Nothing changes
              until a code checks out, so a mistyped secret cannot lock you out.
            </div>
            <input className="mono" readOnly value={setup.secret} style={{ marginBottom: 8 }} />
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <input
                value={code}
                inputMode="numeric"
                placeholder="123456"
                onChange={(event) => setCode(event.target.value)}
                style={{ flex: 1, minWidth: 140 }}
              />
              <button className="btn btn-primary" disabled={code.length < 6} onClick={() => void confirm()}>
                Confirm
              </button>
              <button className="btn" onClick={() => setSetup(null)}>Cancel</button>
            </div>
          </>
        ) : (
          <>
            <div className="field-hint" style={{ marginBottom: 10 }}>
              Off. Your password is the only thing protecting this server from the internet.
            </div>
            <button className="btn" onClick={() => void begin()}>Turn on</button>
          </>
        )}
      </div>

      {error ? <div className="notice notice-error" style={{ marginBottom: 14 }}>{error}</div> : null}

      <button className="btn" onClick={() => void logout()}>Sign out</button>
    </div>
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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      // The ceiling is reported by the storage panel too.
      queryClient.invalidateQueries({ queryKey: ["storage"] });
    },
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
          own. This is a target, not a floor: lowering it removes the excess, and 0
          reclaims everything auto-download fetched. Queued episodes and anything you
          downloaded by hand are never removed. Mind the disk on shows with long episodes.
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

/** New-episode notifications.
 *
 *  Every failure mode here is silent and remote -- permission granted to a different
 *  origin, a key that does not match the subscription, an endpoint the push service has
 *  quietly retired -- so the panel leans on one test button that either buzzes the device
 *  or does not.
 */
function NotificationsPanel() {
  const queryClient = useQueryClient();
  const { data: config } = useQuery({ queryKey: ["push-config"], queryFn: api.pushConfig });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const supported =
    typeof window !== "undefined" &&
    "Notification" in window &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    window.isSecureContext;

  const enable = async () => {
    setError(null);
    setBusy(true);
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setError("Notifications were not allowed. Your browser will not ask again until you change it in site settings.");
        return;
      }
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.subscribe({
        // Required to be true by every browser: a push that shows nothing is not allowed.
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(config!.public_key!),
      });
      await api.pushSubscribe({ ...describeSubscription(subscription), label: navigator.userAgent.slice(0, 80) });
      await queryClient.invalidateQueries({ queryKey: ["push-config"] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const disable = async () => {
    setError(null);
    setBusy(true);
    try {
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      if (subscription) {
        await subscription.unsubscribe();
        await api.pushUnsubscribe(subscription.endpoint);
      } else {
        // No local subscription to name, so clear every device rather than leave rows
        // behind that nothing can reach.
        await api.pushUnsubscribe();
      }
      await queryClient.invalidateQueries({ queryKey: ["push-config"] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel">
      <div className="panel-title">Notifications</div>
      <p className="panel-hint">
        A single notification per refresh when new episodes arrive — not one per show.
      </p>

      {!supported ? (
        <div className="field-hint">
          This browser cannot receive push notifications. On iOS they only work once
          Podarium has been added to the Home Screen, and only over HTTPS.
        </div>
      ) : !config ? null : !config.public_key ? (
        <div className="field-hint">
          The server has no VAPID keys, so push is switched off. Generate a pair with{" "}
          <code className="mono">python -m podarium.vapid</code> and set{" "}
          <code className="mono">VAPID_PUBLIC_KEY</code> and{" "}
          <code className="mono">VAPID_PRIVATE_KEY</code>.
        </div>
      ) : config.subscribed ? (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn" disabled={busy} onClick={() => void api.pushTest()}>
            Send a test
          </button>
          <button className="btn btn-danger" disabled={busy} onClick={() => void disable()}>
            Turn off
          </button>
        </div>
      ) : (
        <button className="btn" disabled={busy} onClick={() => void enable()}>
          {busy ? "Enabling…" : "Turn on"}
        </button>
      )}

      {error ? (
        <div className="notice notice-error" style={{ marginTop: 12 }}>{error}</div>
      ) : null}
    </div>
  );
}

/** What is on this device, and the honest limits of it. */
function OfflinePanel() {
  const offline = useOffline();

  return (
    <div className="panel">
      <div className="panel-title">Offline</div>
      <p className="panel-hint">
        Podarium streams from your server, so without a network there is nothing to play.
        Episodes kept on this device are the exception — they are stored in the browser and
        play with no connection at all.
      </p>

      {!offline.supported ? (
        <div className="field-hint">
          This browser cannot store episodes. It needs a service worker, which means HTTPS —
          over plain HTTP on the LAN, only streaming works.
        </div>
      ) : (
        <>
          <div className="field-hint">
            {offline.saved.size === 0
              ? "Nothing kept on this device yet. Use the phone button on any downloaded episode."
              : `${offline.saved.size} episode${offline.saved.size === 1 ? "" : "s"} kept on this device.`}
          </div>
          <div className="field-hint" style={{ marginTop: 8 }}>
            Storage here is granted by the browser and it can be reclaimed without warning —
            iOS in particular is aggressive about it. Treat it as a convenience for a
            journey, not as a copy you can rely on.
          </div>
        </>
      )}

      {offline.error ? (
        <div className="notice notice-error" style={{ marginTop: 12 }}>{offline.error}</div>
      ) : null}
    </div>
  );
}
