export const pct = (v: number | null | undefined) => (v === null || v === undefined ? "n/a" : `${(v * 100).toFixed(1)}%`);
export const usd = (v: number) => (v < 0.01 && v > 0 ? `$${v.toFixed(5)}` : `$${v.toFixed(2)}`);
export const inr = (v: number) => `Rs ${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
export const ms = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${v} ms`);

export function timeAgo(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

export const STATUS_LABEL: Record<string, string> = {
  open: "AI handling",
  needs_human: "Needs human",
  human: "Agent handling",
  resolved: "Resolved",
};

export const titleCase = (s: string | null | undefined) =>
  s ? s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "";
