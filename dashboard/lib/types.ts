export type TicketStatus = "open" | "needs_human" | "human" | "resolved";

export type TicketSummary = {
  id: number;
  subject: string;
  status: TicketStatus;
  priority: "low" | "normal" | "high";
  intent: string | null;
  sentiment: string | null;
  language: string | null;
  channel: string;
  customer: { id: number; name: string; tier: string; verified: boolean; telegram: string | null; email?: string | null; memory?: string };
  last_message: { role: string; content: string; at: string } | null;
  pending_approvals: number;
  escalation_reason: string | null;
  csat: number | null;
  created_at: string;
  updated_at: string;
};

export type ChatMessage = {
  id: number;
  role: "customer" | "ai" | "agent" | "system";
  content: string;
  is_voice: boolean;
  sources: { n: number; doc: string; section: string }[] | null;
  confidence: number | null;
  latency_ms: number | null;
  cost_usd: number;
  at: string;
};

export type ApprovalItem = {
  id: number;
  ticket_id?: number;
  order_id: string;
  action: string;
  amount_inr: number;
  reason: string;
  status: "pending" | "approved" | "rejected";
  customer?: string;
  decided_by: string | null;
  created_at?: string;
};

export type TicketDetail = TicketSummary & {
  messages: ChatMessage[];
  orders: { id: string; status: string; total_inr: number; items: string[]; placed: string }[];
  approvals: ApprovalItem[];
  total_cost_usd: number;
};

export type Overview = {
  window_days: number;
  tickets: { total: number; by_status: Record<string, number>; escalated: number; resolved: number; resolved_by_ai: number; intents: Record<string, number> };
  rates: { ai_resolution_rate: number | null; escalation_rate: number | null; cache_hit_rate: number | null; llm_error_rate: number | null };
  latency_ms: { avg: number; p50: number; p95: number };
  cost: {
    total_usd: number; per_ticket_usd: number; input_tokens: number; output_tokens: number; cached_tokens: number; llm_calls: number;
    today_usd: number; daily_budget_usd: number;
    by_model: { model: string; calls: number; cost_usd: number; avg_latency_ms: number }[];
    by_purpose: { purpose: string; calls: number; cost_usd: number }[];
  };
  csat: { average: number | null; responses: number };
  guardrails: Record<string, number>;
  series: { date: string; tickets: number; escalations: number; cost_usd: number }[];
  latest_eval: { at: string; provider: string; passed: boolean; metrics: Record<string, unknown> } | null;
  provider: string;
  models: { fast: string; smart: string };
};

export type EvalRun = { id: number; at: string; provider: string; passed: boolean; metrics: Record<string, unknown> };
