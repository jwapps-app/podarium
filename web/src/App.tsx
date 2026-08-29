import { useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { refreshBadge } from "./lib/badge";
import { Layout } from "./components/Layout";
import { Loading } from "./components/Loading";
import { useAuth } from "./lib/auth";
import { PlayerProvider } from "./lib/player";
import { FeedDetailPage } from "./pages/FeedDetail";
import { InboxPage } from "./pages/Inbox";
import { LibraryPage } from "./pages/Library";
import { LoginPage } from "./pages/Login";
import { QueuePage } from "./pages/Queue";
import { SearchPage } from "./pages/Search";
import { SettingsPage } from "./pages/Settings";
import { StarredPage } from "./pages/Starred";

export function App() {
  const { user, loading } = useAuth();

  // Correct the icon whenever the app is actually running. Between pushes it shows
  // whatever the last one said -- iOS gives a web app no background execution -- so a
  // message that was dropped, or delivered to a different device, leaves it wrong until
  // someone asks the server. Opening the app and returning to it are those moments.
  useEffect(() => {
    if (!user) return;
    void refreshBadge();
    const onVisible = () => {
      if (document.visibilityState === "visible") void refreshBadge();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [user]);

  if (loading) return <Loading label="Starting Podarium" />;
  if (!user) return <LoginPage />;

  return (
    <PlayerProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<LibraryPage />} />
            <Route path="inbox" element={<InboxPage />} />
            <Route path="starred" element={<StarredPage />} />
            <Route path="queue" element={<QueuePage />} />
            <Route path="search" element={<SearchPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="feeds/:id" element={<FeedDetailPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </PlayerProvider>
  );
}
