import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Everything is proxied to the API in dev so the browser sees a single origin, exactly as
// it will in production. That matters for more than convenience: the session cookie is
// httpOnly, and <audio src="/api/stream/..."> only carries it on a same-origin request.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8044", changeOrigin: true },
      "/healthz": "http://127.0.0.1:8044",
      "/metrics": "http://127.0.0.1:8044",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    // The sanitiser walks a real DOM, so it needs one.
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
  },
});
