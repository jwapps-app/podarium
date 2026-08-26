import { useState } from "react";

import { ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { BrandMark } from "../components/Icons";

export function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  // Only asked for once the server says the account has a second factor, so the form does
  // not present a field most sign-ins do not need.
  const [totpCode, setTotpCode] = useState("");
  const [totpRequired, setTotpRequired] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password, totpCode);
    } catch (cause) {
      if (cause instanceof ApiError && cause.message === "totp_required") {
        setTotpRequired(true);
        setError(null);
      } else if (cause instanceof ApiError && cause.isRateLimited) {
        // Its own message, not a wrong-password one: retrying immediately is exactly the
        // wrong response, and only this case can say so.
        const seconds = Number(/(\d+) seconds/.exec(cause.message)?.[1] ?? 0);
        const minutes = Math.ceil(seconds / 60);
        setError(
          seconds > 90
            ? `Too many failed sign-ins. Try again in about ${minutes} minutes.`
            : "Too many failed sign-ins. Try again in a moment.",
        );
      } else if (cause instanceof ApiError && cause.isUnauthorized) {
        setError("That username and password did not match.");
      } else {
        setError(
          `Could not sign in: ${cause instanceof Error ? cause.message : String(cause)}`,
        );
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-shell">
      <form className="card login-card" onSubmit={submit}>
        <div className="login-brand">
          <BrandMark className="brand-mark" />
          Podarium
        </div>
        <p className="login-tagline">Your podcasts, fetched by your server.</p>

        <div className="field">
          <label htmlFor="username">Username</label>
          <input
            id="username"
            value={username}
            autoComplete="username"
            autoFocus
            onChange={(event) => setUsername(event.target.value)}
          />
        </div>

        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            value={password}
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>

        {totpRequired ? (
          <div className="field">
            <label htmlFor="totp">Authentication code</label>
            <input
              id="totp"
              value={totpCode}
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="123456"
              autoFocus
              onChange={(event) => setTotpCode(event.target.value)}
            />
            <div className="field-hint">The six digits from your authenticator app.</div>
          </div>
        ) : null}

        {error ? <div className="notice notice-error" style={{ marginBottom: 14 }}>{error}</div> : null}

        <button className="btn btn-primary" type="submit" disabled={busy || !username || !password} style={{ width: "100%" }}>
          {busy ? "Signing in…" : totpRequired ? "Verify" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
