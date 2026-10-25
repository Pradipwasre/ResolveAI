"use client";

import { useCallback, useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useLive } from "@/components/shell";
import { Badge, Card, PageHeader, Spinner, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { ms, pct, titleCase, usd } from "@/lib/format";
import type { Overview } from "@/lib/types";

const AXIS = { fontSize: 12, fill: "#64748b" };

export default function AnalyticsPage() {
  const [days, setDays] = useState(7);
  const [data, setData] = useState<Overview | null>(null);
  const load = useCallback(() => {
    api<Overview>(`/api/metrics/overview?days=${days}`).then(setData);
  }, [days]);
  useEffect(load, [load]);
  useLive(useCallback((e) => e.event === "message" && load(), [load]));

  if (!data) return <Spinner />;
  const budgetUsed = data.cost.daily_budget_usd ? data.cost.today_usd / data.cost.daily_budget_usd : 0;
  const series = data.series.map((s) => ({ ...s, day: s.date.slice(5) }));
  const intents = Object.entries(data.tickets.intents).map(([intent, count]) => ({ intent: titleCase(intent), count }));

  return (
    <div className="mx-auto max-w-7xl p-8">
      <PageHeader
        title="Analytics"
        description={`Quality, speed and cost of the support assistant. Provider: ${data.provider}, models: ${data.models.fast} (fast) and ${data.models.smart} (smart).`}
        action={
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm">
            <option value={1}>Last 24 hours</option>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
          </select>
        }
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Tickets" value={data.tickets.total} hint={`${data.tickets.resolved} resolved`} />
        <Stat label="AI resolution rate" value={pct(data.rates.ai_resolution_rate)} hint="Resolved without a human" />
        <Stat label="Escalation rate" value={pct(data.rates.escalation_rate)} hint={`${data.tickets.escalated} handed to humans`} />
        <Stat label="Customer rating" value={data.csat.average ? `${data.csat.average} / 5` : "n/a"} hint={`${data.csat.responses} ratings`} />
        <Stat label="Reply time p50 / p95" value={`${ms(data.latency_ms.p50)} / ${ms(data.latency_ms.p95)}`} hint={`average ${ms(data.latency_ms.avg)}`} />
        <Stat label="Cost per ticket" value={usd(data.cost.per_ticket_usd)} hint={`${usd(data.cost.total_usd)} total`} />
        <Stat label="Spend today" value={usd(data.cost.today_usd)} hint={`${pct(budgetUsed)} of ${usd(data.cost.daily_budget_usd)} daily budget`} />
        <Stat label="Answer cache hit rate" value={pct(data.rates.cache_hit_rate)} hint={`LLM error rate ${pct(data.rates.llm_error_rate)}`} />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card title="Tickets and escalations per day">
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={series}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="day" tick={AXIS} />
                <YAxis allowDecimals={false} tick={AXIS} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="tickets" name="Tickets" fill="#6366f1" radius={[3, 3, 0, 0]} />
                <Bar dataKey="escalations" name="Escalations" fill="#f59e0b" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="AI cost per day (USD)">
          <div className="h-64">
            <ResponsiveContainer>
              <LineChart data={series}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="day" tick={AXIS} />
                <YAxis tick={AXIS} tickFormatter={(v) => `$${Number(v).toFixed(3)}`} width={64} />
                <Tooltip formatter={(v) => usd(Number(v))} />
                <Line type="linear" dataKey="cost_usd" name="Cost" stroke="#4f46e5" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card title="Cost by model">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr><th className="pb-2">Model</th><th className="pb-2 text-right">Calls</th><th className="pb-2 text-right">Cost</th><th className="pb-2 text-right">Avg</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.cost.by_model.map((m) => (
                <tr key={m.model}>
                  <td className="py-2 font-medium">{m.model}</td>
                  <td className="py-2 text-right">{m.calls}</td>
                  <td className="py-2 text-right">{usd(m.cost_usd)}</td>
                  <td className="py-2 text-right text-slate-500">{ms(m.avg_latency_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-3 text-xs text-slate-500">
            Tokens: {data.cost.input_tokens.toLocaleString()} in ({data.cost.cached_tokens.toLocaleString()} cached),{" "}
            {data.cost.output_tokens.toLocaleString()} out, {data.cost.llm_calls} calls.
          </p>
        </Card>
        <Card title="Cost by step">
          <table className="w-full text-sm">
            <tbody className="divide-y divide-slate-100">
              {data.cost.by_purpose.map((p) => (
                <tr key={p.purpose}>
                  <td className="py-2">{titleCase(p.purpose)}</td>
                  <td className="py-2 text-right text-slate-500">{p.calls} calls</td>
                  <td className="py-2 text-right">{usd(p.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Guardrail events">
          {Object.keys(data.guardrails).length === 0 ? (
            <p className="text-sm text-slate-500">No guardrail events in this period.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {Object.entries(data.guardrails).map(([kind, count]) => (
                <li key={kind} className="flex justify-between">
                  <span>{titleCase(kind)}</span>
                  <span className="font-medium">{count}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card title="What customers ask about">
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={intents} layout="vertical" margin={{ left: 40 }}>
                <XAxis type="number" allowDecimals={false} tick={AXIS} />
                <YAxis type="category" dataKey="intent" tick={AXIS} width={110} />
                <Tooltip />
                <Bar dataKey="count" name="Tickets" fill="#6366f1" radius={[0, 3, 3, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Latest evaluation">
          {data.latest_eval ? (
            <div className="space-y-2 text-sm">
              <div className="flex items-center gap-2">
                <Badge tone={data.latest_eval.passed ? "green" : "red"}>{data.latest_eval.passed ? "Passed" : "Failed"}</Badge>
                <span className="text-slate-500">{new Date(data.latest_eval.at).toLocaleString()} ({data.latest_eval.provider})</span>
              </div>
              {["retrieval_hit_rate", "faithfulness", "answer_relevance", "intent_accuracy", "injection_recall"].map((k) => (
                <div key={k} className="flex justify-between border-b border-slate-100 py-1.5">
                  <span>{titleCase(k)}</span>
                  <span className="font-medium">{pct(data.latest_eval?.metrics[k] as number)}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-500">No evaluation has been run yet. Run one from the Evaluations page.</p>
          )}
        </Card>
      </div>
    </div>
  );
}
