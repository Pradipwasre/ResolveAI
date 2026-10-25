"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Card, Empty, PageHeader, Spinner } from "@/components/ui";
import { api, post } from "@/lib/api";
import { useInterval } from "@/lib/live";
import { titleCase } from "@/lib/format";
import type { EvalRun } from "@/lib/types";

const METRICS = [
  ["retrieval_hit_rate", "Right article in the top results"],
  ["retrieval_mrr", "How high the right article ranks"],
  ["faithfulness", "Answer claims supported by sources (LLM judge)"],
  ["answer_relevance", "Answer addresses the question (LLM judge)"],
  ["keyword_coverage", "Key facts present in the answer"],
  ["intent_accuracy", "Classifier picks the right intent"],
  ["injection_recall", "Prompt-injection attacks blocked"],
  ["injection_false_positive_rate", "Normal messages wrongly blocked (lower is better)"],
  ["pii_accuracy", "Personal data detected and masked"],
] as const;

export default function EvalsPage() {
  const [runs, setRuns] = useState<EvalRun[] | null>(null);
  const [running, setRunning] = useState(false);
  const load = useCallback(() => {
    api<{ running: boolean; runs: EvalRun[] }>("/api/evals").then((r) => {
      setRuns(r.runs);
      setRunning(r.running);
    });
  }, []);
  useEffect(load, [load]);
  useInterval(() => running && load(), 3000);

  const latest = runs?.[0];
  const thresholds = (latest?.metrics.thresholds ?? {}) as Record<string, number>;

  return (
    <div className="mx-auto max-w-6xl p-8">
      <PageHeader
        title="Evaluations"
        description="An offline test suite of 33 support questions, 20 intent examples and 25 safety cases. The same suite runs in CI on every push, and a failed threshold fails the build."
        action={
          <Button loading={running} onClick={() => post("/api/evals/run").then(() => { setRunning(true); load(); })}>
            {running ? "Running" : "Run evaluation"}
          </Button>
        }
      />
      {runs === null ? (
        <Spinner />
      ) : !latest ? (
        <Card><Empty title="No evaluation runs yet" body="Run the suite to measure retrieval, answer quality, routing and safety." /></Card>
      ) : (
        <div className="grid gap-6 lg:grid-cols-3">
          <Card
            className="lg:col-span-2"
            title="Latest run"
            action={<Badge tone={latest.passed ? "green" : "red"}>{latest.passed ? "All thresholds passed" : "Thresholds failed"}</Badge>}
          >
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
                <tr><th className="pb-2">Metric</th><th className="pb-2 text-right">Score</th><th className="pb-2 text-right">Threshold</th><th /></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {METRICS.map(([key, label]) => {
                  const value = latest.metrics[key] as number | undefined;
                  const limit = thresholds[key];
                  const lowerBetter = key.endsWith("false_positive_rate");
                  const ok = value === undefined || limit === undefined || (lowerBetter ? value <= limit : value >= limit);
                  return (
                    <tr key={key}>
                      <td className="py-2.5">
                        <p className="font-medium">{titleCase(key)}</p>
                        <p className="text-xs text-slate-500">{label}</p>
                      </td>
                      <td className="py-2.5 text-right font-medium">{value === undefined ? "n/a" : value.toFixed(3)}</td>
                      <td className="py-2.5 text-right text-slate-500">{limit === undefined ? "" : `${lowerBetter ? "<=" : ">="} ${limit}`}</td>
                      <td className="py-2.5 pl-3 text-right"><Badge tone={ok ? "green" : "red"}>{ok ? "Pass" : "Fail"}</Badge></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>
          <Card title="History">
            <ul className="divide-y divide-slate-100 text-sm">
              {runs.map((r) => (
                <li key={r.id} className="flex items-center justify-between py-2">
                  <div>
                    <p>{new Date(r.at).toLocaleString()}</p>
                    <p className="text-xs text-slate-500">provider: {r.provider}</p>
                  </div>
                  <Badge tone={r.passed ? "green" : "red"}>{r.passed ? "Passed" : "Failed"}</Badge>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}
    </div>
  );
}
