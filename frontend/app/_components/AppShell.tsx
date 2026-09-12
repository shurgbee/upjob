"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { BellIcon, BriefcaseIcon, CloseIcon, CoinIcon, DocumentIcon, FlameIcon, GiftIcon, MenuIcon, SparkIcon } from "./Icons";

const navigation = [
  { label: "Jobs", href: "/home", icon: BriefcaseIcon },
  { label: "Resume", href: "/home#resume", icon: DocumentIcon },
  { label: "Rewards", href: "/shop", icon: GiftIcon },
  { label: "Skills", href: "/home#skills", icon: SparkIcon },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const pathname = usePathname();

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
          <Link href="/shop" className="coin-balance" aria-label="12 reward coins, open shop"><span>12</span><CoinIcon /></Link>
          <button className="icon-button notification-button" type="button" aria-label="Notifications"><BellIcon /><span className="notification-dot" /></button>
          <button className="avatar" type="button" aria-label="Open Jordan's profile">JD</button>
        </div>
      </header>

      <div className={`drawer-backdrop ${menuOpen ? "is-open" : ""}`} onClick={() => setMenuOpen(false)} />
      <aside className={`side-drawer ${menuOpen ? "is-open" : ""}`} aria-hidden={!menuOpen}>
        <div className="drawer-header">
          <Link href="/home" className="wordmark drawer-wordmark">upjob<span className="wordmark-smile" /></Link>
          <button className="icon-button" type="button" onClick={() => setMenuOpen(false)} aria-label="Close navigation"><CloseIcon /></button>
        </div>
        <nav className="drawer-nav" aria-label="Main navigation">
          {navigation.map(({ label, href, icon: Icon }) => (
            <Link className={pathname === href ? "is-active" : ""} href={href} key={label} onClick={() => setMenuOpen(false)}>
              <Icon /><span>{label}</span>
            </Link>
          ))}
        </nav>
        <div className="drawer-streak"><FlameIcon /><div><strong>7 day streak</strong><span>Keep the momentum going.</span></div></div>
      </aside>

      <main className="page-content">{children}</main>
    </div>
  );
}
