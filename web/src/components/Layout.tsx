import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../lib/auth";
import { useFeeds, useQueue } from "../lib/queries";
import {
  BrandMark,
  InboxIcon,
  LibraryIcon,
  LogoutIcon,
  QueueIcon,
  SearchIcon,
  SettingsIcon,
  StarIcon,
} from "./Icons";
import { NowPlaying } from "./NowPlaying";
import { PlayerBar } from "./PlayerBar";

export function Layout() {
  const { logout } = useAuth();
  const { data: queue } = useQueue();
  const { data: feeds } = useFeeds();

  // The sum of the library tiles' badges, so the two always agree: whatever this says, you
  // get the same number by adding up the shows. Counting unplayed instead -- which is what
  // this used to do -- pins it at "199+" forever, because a subscription's back catalogue
  // is a backlog nobody intends to finish rather than a list of things to deal with.
  const newCount = (feeds ?? [])
    .filter((feed) => feed.active)
    .reduce((total, feed) => total + (feed.new_episode_count ?? 0), 0);

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
          {newCount > 0 ? (
            <span className="nav-count" title={`${newCount} new since you last opened these shows`}>
              {newCount > 99 ? "99+" : newCount}
            </span>
          ) : null}
        </NavLink>

        <NavLink to="/starred" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
          <StarIcon />
          Starred
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

        {/* Desktop only. The phone's tab bar has no room for a seventh item, and signing
            out is a rare action that should not sit under a thumb -- it lives on the
            settings page there instead. */}
        <button
          className="nav-link nav-desktop-only"
          onClick={() => void logout()}
          style={{ border: "none", background: "none", width: "100%" }}
        >
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
