"use client";

import { type FormEvent, useEffect, useState } from "react";
import { Badge, Button, Card, PageHeader, Spinner } from "@/components/ui";
import { api, post } from "@/lib/api";
import { timeAgo } from "@/lib/format";

type Doc = { doc_id: string; title: string; chunks: number; indexed_at: string };
type Hit = { doc: string; section: string; content: string; fused_score: number; similarity: number; keyword_hit: boolean };

export default function KnowledgePage() {
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [reindexing, setReindexing] = useState(false);
  const [result, setResult] = useState("");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [searching, setSearching] = useState(false);

  const load = () => api<Doc[]>("/api/knowledge").then(setDocs);
  useEffect(() => {
    load();
  }, []);

  async function reindex() {
    setReindexing(true);
    try {
      const r = await post<{ added: number; removed: number; total: number }>("/api/knowledge/reindex");
      setResult(`${r.added} chunks added, ${r.removed} removed, ${r.total} in the index.`);
      load();
    } finally {
      setReindexing(false);
    }
  }

  async function search(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    try {
      setHits(await api<Hit[]>(`/api/knowledge/search?q=${encodeURIComponent(query)}`));
    } finally {
      setSearching(false);
    }
  }

  return (
    <div className="mx-auto max-w-6xl p-8">
      <PageHeader
        title="Knowledge base"
        description="Help articles the assistant answers from. Edit the Markdown files in backend/data/knowledge, then re-index. Unchanged sections are not embedded again."
        action={<Button loading={reindexing} onClick={reindex}>Re-index articles</Button>}
      />
      {result && <p className="mb-4 text-sm text-emerald-700">{result}</p>}
      <div className="grid gap-6 lg:grid-cols-5">
        <Card title="Indexed articles" className="lg:col-span-2">
          {docs === null ? (
            <Spinner />
          ) : (
            <ul className="divide-y divide-slate-100 text-sm">
              {docs.map((d) => (
                <li key={d.doc_id} className="flex items-center justify-between py-2.5">
                  <div>
                    <p className="font-medium">{d.title}</p>
                    <p className="text-xs text-slate-500">{d.doc_id}.md, indexed {timeAgo(d.indexed_at)}</p>
                  </div>
                  <Badge>{d.chunks} chunks</Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Retrieval debugger" className="lg:col-span-3">
          <form onSubmit={search} className="flex gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Try: how long do card refunds take"
              className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-brand-500"
            />
            <Button type="submit" loading={searching}>Search</Button>
          </form>
          <p className="mt-2 text-xs text-slate-500">
            Shows what hybrid search (vector similarity plus keyword match, fused with reciprocal rank fusion) returns for a question.
          </p>
          <div className="mt-4 space-y-3">
            {hits?.map((h, i) => (
              <div key={i} className="rounded-md border border-slate-200 p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{i + 1}. {h.doc}: {h.section}</span>
                  <Badge tone="blue">similarity {h.similarity.toFixed(2)}</Badge>
                  <Badge>fused {h.fused_score.toFixed(4)}</Badge>
                  {h.keyword_hit && <Badge tone="green">keyword match</Badge>}
                </div>
                <p className="mt-2 line-clamp-3 text-slate-600">{h.content.split("\n").slice(1).join(" ")}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
