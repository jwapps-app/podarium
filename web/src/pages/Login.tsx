import { useState } from "react";

import { ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { BrandMark } from "../components/Icons";

export function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
    } catch (cause) {
      setError(
        cause instanceof ApiError && cause.isUnauthorized
          ? "That username and password did not match."
          : `Could not sign in: ${cause instanceof Error ? cause.message : String(cause)}`,
      );
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

        {error ? <div className="notice notice-error" style={{ marginBottom: 14 }}>{error}</div> : null}

        <button className="btn btn-primary" type="submit" disabled={busy || !username || !password} style={{ width: "100%" }}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
