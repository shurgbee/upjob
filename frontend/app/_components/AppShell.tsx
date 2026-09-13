"use client";

import { useAuth } from "@workos-inc/authkit-nextjs/components";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useSyncExternalStore } from "react";
import { BellIcon, BriefcaseIcon, CloseIcon, DocumentIcon, FlameIcon, GiftIcon, MenuIcon, MoonIcon, SparkIcon, SunIcon } from "./Icons";

const navigation = [
  { label: "Jobs", href: "/home", icon: BriefcaseIcon },
  { label: "Resume", href: "/resume", icon: DocumentIcon },
  { label: "Rewards", href: "/shop", icon: GiftIcon },
  { label: "Skills", href: "/skills", icon: SparkIcon },
];

type Theme = "light" | "dark";
const themeStorageKey = "upjob-theme";

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  window.dispatchEvent(new Event("upjob-theme-change"));
}

function getTheme(): Theme {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function subscribeToTheme(onStoreChange: () => void) {
  const handleStorage = (event: StorageEvent) => {
    if (event.key !== themeStorageKey) return;
    applyTheme(event.newValue === "dark" ? "dark" : "light");
  };
  window.addEventListener("storage", handleStorage);
  window.addEventListener("upjob-theme-change", onStoreChange);
  return () => {
    window.removeEventListener("storage", handleStorage);
    window.removeEventListener("upjob-theme-change", onStoreChange);
  };
}

export function AppShell({ children, showGamification = true }: { children: React.ReactNode; showGamification?: boolean }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const pathname = usePathname();
  const { user, signOut } = useAuth({ ensureSignedIn: true });
  const email = user?.email ?? "";
  const displayName = user?.name ?? ([user?.firstName, user?.lastName].filter(Boolean).join(" ") || email || "Account");
  const initials = (user?.firstName?.[0] ?? email[0] ?? "U") + (user?.lastName?.[0] ?? "");
  const profilePictureUrl = user?.profilePictureUrl;
  const theme = useSyncExternalStore(subscribeToTheme, getTheme, () => "light");

  function changeTheme(nextTheme: Theme) {
    window.localStorage.setItem(themeStorageKey, nextTheme);
    applyTheme(nextTheme);
  }

  async function handleSignOut() {
    setSigningOut(true);
    await signOut({ returnTo: `${window.location.origin}/login` });
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="icon-button menu-button" type="button" onClick={() => setMenuOpen(true)} aria-label="Open navigation" aria-expanded={menuOpen}>
          <MenuIcon />
        </button>
        <Link href="/home" className="wordmark" aria-label="UpJob home">
          <span>upjob</span><span className="wordmark-smile" aria-hidden="true" />
        </Link>
        <div className="topbar-actions">
          <button className="icon-button notification-button" type="button" aria-label="Notifications"><BellIcon /><span className="notification-dot" /></button>
          <div className="profile-menu-wrap">
            <button className="avatar" type="button" aria-label={`Open ${displayName}'s profile`} aria-expanded={profileOpen} onClick={() => setProfileOpen((open) => !open)}>
              {profilePictureUrl ? (
                // Google profile URLs are external and user-specific, so use the source URL directly.
                // eslint-disable-next-line @next/next/no-img-element
                <img src={profilePictureUrl} alt="" referrerPolicy="no-referrer" />
              ) : initials.toUpperCase()}
            </button>
            {profileOpen && (
              <div className="profile-menu">
                <div className="profile-summary"><span className="profile-avatar">{profilePictureUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={profilePictureUrl} alt="" referrerPolicy="no-referrer" />
                ) : initials.toUpperCase()}</span><div><strong>{displayName}</strong><span>{email}</span></div></div>
                <button type="button" onClick={handleSignOut} disabled={signingOut}>{signingOut ? "Signing out…" : "Sign out"}</button>
              </div>
            )}
          </div>
        </div>
      </header>

      <div className={`drawer-backdrop ${menuOpen ? "is-open" : ""}`} onClick={() => setMenuOpen(false)} />
      <aside className={`side-drawer ${menuOpen ? "is-open" : ""}`} aria-hidden={!menuOpen}>
        <div className="drawer-header">
          <Link href="/home" className="wordmark drawer-wordmark">upjob<span className="wordmark-smile" /></Link>
          <button className="icon-button" type="button" onClick={() => setMenuOpen(false)} aria-label="Close navigation"><CloseIcon /></button>
        </div>
        <nav className="drawer-nav" aria-label="Main navigation">
          {navigation.filter(({ href }) => showGamification || href !== "/shop").map(({ label, href, icon: Icon }) => (
            <Link className={pathname === href ? "is-active" : ""} href={href} key={label} onClick={() => setMenuOpen(false)}>
              <Icon /><span>{label}</span>
            </Link>
          ))}
        </nav>
        {showGamification && <div className="drawer-streak"><FlameIcon /><div><strong>7 day streak</strong><span>Keep the momentum going.</span></div></div>}
        <div className="drawer-footer">
          <section className="theme-filter" aria-label="Color mode">
            <span>Color mode</span>
            <div role="group" aria-label="Choose color mode">
              <button type="button" className={theme === "light" ? "is-selected" : ""} aria-pressed={theme === "light"} onClick={() => changeTheme("light")}><SunIcon />Light</button>
              <button type="button" className={theme === "dark" ? "is-selected" : ""} aria-pressed={theme === "dark"} onClick={() => changeTheme("dark")}><MoonIcon />Dark</button>
            </div>
          </section>
          <div className="drawer-account"><span>{displayName}</span><button type="button" onClick={() => void handleSignOut()} disabled={signingOut}>{signingOut ? "Signing out…" : "Sign out"}</button></div>
        </div>
      </aside>

      <main className="page-content">{children}</main>
    </div>
  );
}
