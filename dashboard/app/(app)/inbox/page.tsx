"use client";

import clsx from "clsx";
import { Mic, Search } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useLive } from "@/components/shell";
import { Badge, Button, Empty, Spinner, statusTone } from "@/components/ui";
import { api, post } from "@/lib/api";
import { STATUS_LABEL, clock, inr, ms, timeAgo, titleCase, usd } from "@/lib/format";
import type { ChatMessage, TicketDetail, TicketSummary } from "@/lib/types";

const FILTERS = [
  { key: "active", label: "Active" },
  { key: "needs_human", label: "Needs human" },
  { key: "human", label: "With agent" },
  { key: "open", label: "AI handling" },
  { key: "resolved", label: "Resolved" },
];

function TicketList({ selected, onSelect }: { selected: number | null; onSelect: (id: number) => void }) {
  const [filter, setFilter] = useState("active");
  const [query, setQuery] = useState("");
  const [tickets, setTickets] = useState<TicketSummary[] | null>(null);

  const load = useCallback(() => {
    const params = new URLSearchParams({ status: filter });
    if (query) params.set("q", query);
    api<TicketSummary[]>(`/api/tickets?${params}`).then(setTickets).catch(() => setTickets([]));
  }, [filter, query]);

  useEffect(() => {
    const id = setTimeout(load, query ? 250 : 0);
    return () => clearTimeout(id);
  }, [load, query]);
  useLive(useCallback(() => load(), [load]));

  return (
    <div className="flex w-96 shrink-0 flex-col border-r border-slate-200 bg-white">
      <div className="border-b border-slate-100 p-4">
        <h1 className="text-lg font-semibold">Inbox</h1>
        <div className="relative mt-3">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search customer or subject"
            className="w-full rounded-md border border-slate-300 py-2 pl-8 pr-3 text-sm outline-none focus:border-brand-500"
          />
        </div>
        <div className="mt-3 flex flex-wrap gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={clsx(
                "rounded-md px-2.5 py-1 text-xs font-medium",
                filter === f.key ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100",
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>
      <div className="scroll-thin flex-1 overflow-y-auto">
        {tickets === null ? (
          <Spinner />
        ) : tickets.length === 0 ? (
          <Empty title="No tickets here" body="New Telegram conversations appear here as they arrive." />
        ) : (
          tickets.map((t) => (
            <button
              key={t.id}
              onClick={() => onSelect(t.id)}
              className={clsx(
                "block w-full border-b border-slate-100 px-4 py-3 text-left hover:bg-slate-50",
                selected === t.id && "bg-brand-50/60 hover:bg-brand-50/60",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <p className="truncate text-sm font-medium">{t.customer.name}</p>
                <span className="shrink-0 text-xs text-slate-400">{timeAgo(t.updated_at)}</span>
              </div>
              <p className="mt-0.5 truncate text-sm text-slate-700">{t.subject}</p>
              <p className="mt-0.5 truncate text-xs text-slate-500">{t.last_message?.content}</p>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <Badge tone={statusTone(t.status)}>{STATUS_LABEL[t.status]}</Badge>
                {t.priority === "high" && <Badge tone="red">High priority</Badge>}
                {t.pending_approvals > 0 && <Badge tone="amber">Approval pending</Badge>}
                {t.customer.tier === "plus" && <Badge tone="violet">Plus</Badge>}
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  );
}

function MessageBubble({ m }: { m: ChatMessage }) {
  const mine = m.role !== "customer";
  const label = { customer: "Customer", ai: "AI assistant", agent: "Support agent", system: "System" }[m.role];
  return (
    <div className={clsx("flex", mine ? "justify-end" : "justify-start")}>
      <div className="max-w-[75%]">
        <div className={clsx("mb-1 flex items-center gap-2 text-xs text-slate-500", mine && "justify-end")}>
          <span className="font-medium">{label}</span>
          <span>{clock(m.at)}</span>
          {m.is_voice && (
            <span className="inline-flex items-center gap-1">
              <Mic className="h-3 w-3" /> voice note
            </span>
          )}
        </div>
        <div
          className={clsx(
            "whitespace-pre-wrap rounded-lg px-3.5 py-2.5 text-sm",
            m.role === "customer" && "bg-white ring-1 ring-slate-200",
            m.role === "ai" && "bg-brand-600 text-white",
            m.role === "agent" && "bg-slate-800 text-white",
          )}
        >
          {m.content}
        </div>
        {m.role === "ai" && (m.sources?.length || m.latency_ms) && (
          <div className="mt-1 flex flex-wrap justify-end gap-x-3 gap-y-1 text-xs text-slate-500">
            {m.sources?.map((s) => (
              <span key={`${s.n}-${s.section}`}>
                [{s.n}] {s.doc}: {s.section}
              </span>
            ))}
            {m.confidence !== null && <span>confidence {Math.round(m.confidence * 100)}%</span>}
            {m.latency_ms !== null && <span>{ms(m.latency_ms)}</span>}
            {m.cost_usd > 0 && <span>{usd(m.cost_usd)}</span>}
          </div>
        )}
      </div>
    </div>
  );
}

function TicketView({ id }: { id: number }) {
  const [ticket, setTicket] = useState<TicketDetail | null>(null);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    api<TicketDetail>(`/api/tickets/${id}`).then(setTicket).catch(() => setTicket(null));
  }, [id]);
  useEffect(load, [load]);
  useLive(useCallback((e) => e.ticket_id === id && load(), [id, load]));
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [ticket?.messages.length]);

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    try {
      await fn();
      load();
    } finally {
      setBusy(null);
    }
  }

  if (!ticket) return <Spinner />;
  const resolved = ticket.status === "resolved";

  return (
    <div className="flex min-w-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col bg-slate-50">
        <header className="flex items-center justify-between gap-4 border-b border-slate-200 bg-white px-6 py-4">
          <div className="min-w-0">
            <p className="text-xs text-slate-500">Ticket #{ticket.id} via {titleCase(ticket.channel)}</p>
            <h2 className="truncate text-base font-semibold">{ticket.subject}</h2>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Badge tone={statusTone(ticket.status)}>{STATUS_LABEL[ticket.status]}</Badge>
            {!resolved && ticket.status !== "human" && (
              <Button variant="secondary" loading={busy === "take"} onClick={() => act("take", () => post(`/api/tickets/${id}/takeover`))}>
                Take over
              </Button>
            )}
            {!resolved && ticket.status !== "open" && (
              <Button variant="secondary" loading={busy === "back"} onClick={() => act("back", () => post(`/api/tickets/${id}/handback`))}>
                Hand back to AI
              </Button>
            )}
            {!resolved && (
              <Button loading={busy === "resolve"} onClick={() => act("resolve", () => post(`/api/tickets/${id}/resolve`))}>
                Resolve
              </Button>
            )}
          </div>
        </header>

        {ticket.escalation_reason && !resolved && (
          <div className="border-b border-amber-200 bg-amber-50 px-6 py-2 text-sm text-amber-900">
            Escalated to a human: {ticket.escalation_reason.replace(/_/g, " ")}
          </div>
        )}

        <div className="scroll-thin flex-1 space-y-4 overflow-y-auto px-6 py-6">
          {ticket.messages.map((m) => (
            <MessageBubble key={m.id} m={m} />
          ))}
          <div ref={bottom} />
        </div>

        {ticket.approvals
          .filter((a) => a.status === "pending")
          .map((a) => (
            <div key={a.id} className="mx-6 mb-3 flex items-center justify-between gap-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
              <div className="text-sm">
                <p className="font-medium text-amber-900">
                  Refund request: {inr(a.amount_inr)} for {a.order_id}
                </p>
                <p className="text-amber-800">{a.reason}</p>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button variant="danger" loading={busy === `r${a.id}`}
                  onClick={() => act(`r${a.id}`, () => post(`/api/approvals/${a.id}/decide`, { approve: false }))}>
                  Reject
                </Button>
                <Button loading={busy === `a${a.id}`}
                  onClick={() => act(`a${a.id}`, () => post(`/api/approvals/${a.id}/decide`, { approve: true }))}>
                  Approve refund
                </Button>
              </div>
            </div>
          ))}

        {!resolved && (
          <form
            className="border-t border-slate-200 bg-white p-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (!reply.trim()) return;
              act("reply", () => post(`/api/tickets/${id}/reply`, { text: reply })).then(() => setReply(""));
            }}
          >
            <textarea
              value={reply}
              onChange={(e) => setReply(e.target.value)}
              rows={3}
              placeholder="Reply to the customer on Telegram. Sending a reply takes the ticket over from the AI."
              className="w-full resize-none rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-brand-500"
            />
            <div className="mt-2 flex justify-end">
              <Button type="submit" loading={busy === "reply"} disabled={!reply.trim()}>
                Send reply
              </Button>
            </div>
          </form>
        )}
      </div>

      <aside className="scroll-thin w-72 shrink-0 space-y-5 overflow-y-auto border-l border-slate-200 bg-white p-5 text-sm">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Customer</p>
          <p className="mt-1 font-medium">{ticket.customer.name}</p>
          <p className="text-slate-500">{ticket.customer.email ?? "Not verified yet"}</p>
          {ticket.customer.telegram && <p className="text-slate-500">Telegram: @{ticket.customer.telegram}</p>}
          <div className="mt-2 flex gap-1.5">
            <Badge tone={ticket.customer.verified ? "green" : "slate"}>{ticket.customer.verified ? "Verified" : "Unverified"}</Badge>
            <Badge tone={ticket.customer.tier === "plus" ? "violet" : "slate"}>{titleCase(ticket.customer.tier)}</Badge>
          </div>
        </div>
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">AI analysis</p>
          <dl className="mt-2 grid grid-cols-2 gap-y-1.5">
            <dt className="text-slate-500">Intent</dt>
            <dd>{titleCase(ticket.intent) || "n/a"}</dd>
            <dt className="text-slate-500">Sentiment</dt>
            <dd>{titleCase(ticket.sentiment) || "n/a"}</dd>
            <dt className="text-slate-500">Language</dt>
            <dd>{{ en: "English", hi: "Hindi", hinglish: "Hinglish" }[ticket.language ?? ""] ?? "n/a"}</dd>
            <dt className="text-slate-500">AI cost</dt>
            <dd>{usd(ticket.total_cost_usd)}</dd>
            {ticket.csat && (
              <>
                <dt className="text-slate-500">Rating</dt>
                <dd>{ticket.csat} / 5</dd>
              </>
            )}
          </dl>
        </div>
        {ticket.customer.memory && (
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Long-term memory</p>
            <p className="mt-1 text-slate-600">{ticket.customer.memory}</p>
          </div>
        )}
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Recent orders</p>
          {ticket.orders.length === 0 && <p className="mt-1 text-slate-500">Visible after verification.</p>}
          <ul className="mt-2 space-y-2">
            {ticket.orders.map((o) => (
              <li key={o.id} className="rounded-md border border-slate-200 p-2.5">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{o.id}</span>
                  <span className="text-xs text-slate-500">{inr(o.total_inr)}</span>
                </div>
                <p className="truncate text-slate-600">{o.items.join(", ")}</p>
                <p className="text-xs text-slate-500">{titleCase(o.status)}</p>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </div>
  );
}

function InboxInner() {
  const params = useSearchParams();
  const router = useRouter();
  const selected = params.get("id") ? Number(params.get("id")) : null;
  return (
    <div className="flex h-screen">
      <TicketList selected={selected} onSelect={(id) => router.replace(`/inbox/?id=${id}`)} />
      {selected ? (
        <TicketView key={selected} id={selected} />
      ) : (
        <div className="flex-1">
          <Empty title="Select a ticket" body="Pick a conversation to read it, reply, approve refunds or resolve it." />
        </div>
      )}
    </div>
  );
}

export default function InboxPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <InboxInner />
    </Suspense>
  );
}
