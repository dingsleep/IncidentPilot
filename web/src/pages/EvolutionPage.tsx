import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { CandidateDiff } from "../components/CandidateDiff";
import { CandidateGateLedger } from "../components/CandidateGateLedger";
import { ApiProblemError, api } from "../api/client";

export function EvolutionPage() {
  const query = useQuery({
    queryKey: ["evolution-candidates"],
    queryFn: () => api.listEvolutionCandidates(),
  });
  const items = query.data ?? [];
  const [selectedId, setSelectedId] = useState<string>();

  useEffect(() => {
    if (!selectedId && items[0]) setSelectedId(items[0].id);
  }, [items, selectedId]);

  const selected = items.find((candidate) => candidate.id === selectedId);
  return (
    <section className="evaluation-page evolution-page">
    <header className="evaluation-heading"><div><p className="eyebrow">{"M9 / \u53d7\u63a7\u6f14\u8fdb"}</p><h1>{"\u5019\u9009\u7248\u672c\u6cbb\u7406"}</h1></div><div className="eval-counter"><strong>{items.length.toString().padStart(2, "0")}</strong><span>{"\u5019\u9009"}<br />{"\u8bb0\u5f55"}</span></div></header>
    <p className="evolution-notice">{"\u4e0d\u4f1a\u81ea\u52a8\u664b\u7ea7\uff0cActive Prompt \u59cb\u7ec8\u4fdd\u6301\u4e0d\u53d8\u3002\u6bcf\u4e2a\u5019\u9009\u90fd\u5fc5\u987b\u7ecf\u8fc7\u79bb\u7ebf\u56de\u5f52\u3001\u5f71\u5b50\u8bc4\u6d4b\u548c\u4eba\u5de5\u51b3\u7b56\u3002"}</p>
    {query.isPending && <div className="workbench-loading">{"\u6b63\u5728\u8bfb\u53d6\u5019\u9009\u8bb0\u5f55\u2026"}</div>}
    {query.isError && <EvolutionError error={query.error} />}
    {items.length > 0 && <div className="evolution-layout">
      <aside className="evolution-ledger" aria-label={"\u5019\u9009\u7248\u672c\u5217\u8868"}>
        <div className="ledger-label">{"\u5019\u9009\u8bb0\u5f55"}</div>
        {items.map((candidate) => <button className={candidate.id === selectedId ? "candidate-entry selected" : "candidate-entry"} key={candidate.id} type="button" onClick={() => setSelectedId(candidate.id)}>
          <span>{candidate.kind} / {candidate.status}</span><strong>{candidate.target_component}</strong><small>{candidate.id}<br />{candidate.target_failure_label}</small>
        </button>)}
      </aside>
      <main className="evolution-detail">
        {selected && <article>
          <div className="run-detail-head"><div><p className="eyebrow">{selected.status.toUpperCase()}</p><h2>{selected.id}</h2><small>{selected.kind} · {selected.target_component} · {selected.target_failure_label}</small></div></div>
          <CandidateDiff diff={selected.diff} />
          <CandidateGateLedger records={selected.gate_records} />
        </article>}
      </main>
    </div>}
    </section>
  );
}

function EvolutionError({ error }: { error: Error }) {
  const correlation = error instanceof ApiProblemError ? error.correlationId : undefined;
  return <div className="error-panel">
    <strong>{"\u5019\u9009\u8bb0\u5f55\u8bfb\u53d6\u5931\u8d25"}</strong>
    <p>{error.message}</p>
    {correlation && <small>correlation: {correlation}</small>}
  </div>;
}
