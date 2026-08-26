import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { App } from "./App";
import { ApiError } from "./lib/api";
import { AuthProvider } from "./lib/auth";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      // Downloads land asynchronously, so a visible list should catch up on its own when
      // the user comes back to the tab.
      refetchOnWindowFocus: true,
      retry: (failureCount, error) => {
        // Retrying an auth failure or a missing Podcast Index key just delays the message.
        if (error instanceof ApiError && (error.isUnauthorized || error.isServiceUnavailable)) {
          return false;
        }
        return failureCount < 2;
      },
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
