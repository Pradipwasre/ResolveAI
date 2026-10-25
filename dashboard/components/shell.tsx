"use client";

import clsx from "clsx";
import { BarChart3, BookOpen, CheckSquare, FlaskConical, Inbox, LogOut, MessageSquareText } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, createContext, useContext, useEffect, useState } from "react";
import { type User, getToken, getUser, logout } from "@/lib/api";
import { type LiveEvent, useLiveEvents } from "@/lib/live";

const NAV = [
  { href: "/inbox/", label: "Inbox", icon: Inbox },
  { href: "/approvals/", label: "Approvals", icon: CheckSquare },
  { href: "/analytics/", label: "Analytics", icon: BarChart3 },
  { href: "/knowledge/", label: "Knowledge base", icon: BookOpen },
  { href: "/evals/", label: "Evaluations", icon: FlaskConical },
  { href: "/console/", label: "Test console", icon: MessageSquareText },
];

type Listener = (event: LiveEvent) => void;
const LiveContext = createContext<{ subscribe: (fn: Listener) => () => void; connected: boolean }>({
  subscribe: () => () => {},
  connected: false,
});

/** Components call this to react to live events (new messages, escalations...). */
export function useLive(fn: Listener) {
  const { subscribe } = useContext(LiveContext);
  useEffect(() => subscribe(fn), [subscribe, fn]);
}

const listeners = new Set<Listener>();
const subscribe = (fn: Listener) => {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
};

export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const connected = useLiveEvents((event) => listeners.forEach((fn) => fn(event)));

  useEffect(() => {
    if (!getToken()) router.replace("/login/");
    else setUser(getUser());
  }, [router]);

  if (!user) return null;

  return (
    <LiveContext.Provider value={{ subscribe, connected }}>
      <div className="flex h-screen overflow-hidden">
        <aside className="flex w-60 shrink-0 flex-col border-r border-slate-200 bg-white">
          <div className="flex items-center gap-2 px-5 py-5">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-brand-600 text-sm font-bold text-white">R</div>
            <div>
              <p className="text-sm font-semibold leading-tight">ResolveAI</p>
              <p className="text-xs text-slate-500">UrbanCart support</p>
            </div>
          </div>
          <nav className="flex-1 space-y-0.5 px-3">
            {NAV.map(({ href, label, icon: Icon }) => (
              <Link
                key={href}
                href={href}
                className={clsx(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm",
                  pathname?.startsWith(href.slice(0, -1))
                    ? "bg-brand-50 font-medium text-brand-700"
                    : "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
                )}
              >
                <Icon className="h-4 w-4" />
                {label}
              </Link>
            ))}
          </nav>
          <div className="border-t border-slate-100 p-4">
            <div className="mb-3 flex items-center gap-2 text-xs text-slate-500">
              <span className={clsx("h-2 w-2 rounded-full", connected ? "bg-emerald-500" : "bg-slate-300")} />
              {connected ? "Live updates on" : "Connecting..."}
            </div>
            <p className="truncate text-sm font-medium">{user.name}</p>
            <p className="truncate text-xs text-slate-500">{user.email}</p>
            <button onClick={logout} className="mt-3 flex items-center gap-2 text-xs text-slate-500 hover:text-slate-800">
              <LogOut className="h-3.5 w-3.5" /> Sign out
            </button>
          </div>
        </aside>
        <main className="flex-1 overflow-y-auto">{children}</main>
      </div>
    </LiveContext.Provider>
  );
}
