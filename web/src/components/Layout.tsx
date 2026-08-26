import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../lib/auth";
import { useEpisodes, useQueue } from "../lib/queries";
import {
  BrandMark,
  InboxIcon,
  LibraryIcon,
  LogoutIcon,
  QueueIcon,
  SearchIcon,
  SettingsIcon,
} from "./Icons";
import { NowPlaying } from "./NowPlaying";
import { PlayerBar } from "./PlayerBar";

export function Layout() {
  const { logout } = useAuth();
  const { data: queue } = useQueue();
  const { data: unplayed } = useEpisodes({ unplayed: true, limit: 200 });

  const unplayedCount = unplayed?.items.length ?? 0;

  return (
    <div className="app">
      <nav className="sidebar">
        <NavLink to="/" className="brand">
          <BrandMark className="brand-mark" />
          Podarium
        </NavLink>

        <NavLink to="/" end className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
          <LibraryIcon />
          Library
        </NavLink>

        <NavLink to="/inbox" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
          <InboxIcon />
          Inbox
          {unplayedCount > 0 ? (
            <span className="nav-count">{unplayedCount > 199 ? "199+" : unplayedCount}</span>
          ) : null}
        </NavLink>

        <NavLink to="/queue" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
          <QueueIcon />
          Queue
          {queue && queue.length > 0 ? <span className="nav-count">{queue.length}</span> : null}
        </NavLink>

        <NavLink to="/search" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
          <SearchIcon />
          Search
        </NavLink>

        <div className="nav-spacer" />

        <NavLink to="/settings" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
          <SettingsIcon />
          Settings
        </NavLink>

        <button className="nav-link" onClick={() => void logout()} style={{ border: "none", background: "none", width: "100%" }}>
          <LogoutIcon />
          Sign out
        </button>
      </nav>

      <main className="main">
        <Outlet />
      </main>

      <PlayerBar />
      <NowPlaying />
    </div>
  );
}
