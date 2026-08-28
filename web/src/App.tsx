import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { MediaDebug } from "./components/MediaDebug";
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

  if (loading) return <Loading label="Starting Podarium" />;
  if (!user) return <LoginPage />;

  return (
    <PlayerProvider>
      <BrowserRouter>
        <MediaDebug />
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
