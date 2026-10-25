"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useLive } from "@/components/shell";
import { Badge, Button, Card, Empty, PageHeader, Spinner } from "@/components/ui";
import { api, post } from "@/lib/api";
import { inr, timeAgo, titleCase } from "@/lib/format";
import type { ApprovalItem } from "@/lib/types";

export default function ApprovalsPage() {
  const [status, setStatus] = useState("pending");
  const [items, setItems] = useState<ApprovalItem[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    api<ApprovalItem[]>(`/api/approvals?status=${status}`).then(setItems);
  }, [status]);
  useEffect(load, [load]);
  useLive(useCallback((e) => e.event.startsWith("approval") && load(), [load]));

  async function decide(id: number, approve: boolean) {
    setBusy(`${id}${approve}`);
    try {
      await post(`/api/approvals/${id}/decide`, { approve });
      load();
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-6xl p-8">
      <PageHeader
        title="Approvals"
        description="Refunds proposed by the AI wait here. Money only moves after a person approves."
        action={
          <select value={status} onChange={(e) => setStatus(e.target.value)} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm">
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="all">All</option>
          </select>
        }
      />
      <Card>
        {items === null ? (
          <Spinner />
        ) : items.length === 0 ? (
          <Empty title="Nothing waiting for approval" />
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="pb-3">Customer</th>
                <th className="pb-3">Order</th>
                <th className="pb-3">Amount</th>
                <th className="pb-3">Reason</th>
                <th className="pb-3">Requested</th>
                <th className="pb-3">Status</th>
                <th className="pb-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((a) => (
                <tr key={a.id}>
                  <td className="py-3 font-medium">{a.customer}</td>
                  <td className="py-3">
                    <Link href={`/inbox/?id=${a.ticket_id}`} className="text-brand-600 hover:underline">{a.order_id}</Link>
                  </td>
                  <td className="py-3">{inr(a.amount_inr)}</td>
                  <td className="max-w-xs truncate py-3 text-slate-600" title={a.reason}>{a.reason}</td>
                  <td className="py-3 text-slate-500">{a.created_at && timeAgo(a.created_at)}</td>
                  <td className="py-3">
                    <Badge tone={a.status === "approved" ? "green" : a.status === "rejected" ? "red" : "amber"}>{titleCase(a.status)}</Badge>
                  </td>
                  <td className="py-3 text-right">
                    {a.status === "pending" ? (
                      <div className="flex justify-end gap-2">
                        <Button variant="danger" loading={busy === `${a.id}false`} onClick={() => decide(a.id, false)}>Reject</Button>
                        <Button loading={busy === `${a.id}true`} onClick={() => decide(a.id, true)}>Approve</Button>
                      </div>
                    ) : (
                      <span className="text-xs text-slate-500">{a.decided_by}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
