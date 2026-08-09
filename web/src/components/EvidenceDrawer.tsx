import type { Evidence } from "../api/types";

export function EvidenceDrawer({
  evidence,
  loading,
  onClose,
}: {
  evidence?: Evidence;
  loading: boolean;
  onClose: () => void;
}) {
  return (
    <aside className="evidence-drawer" aria-label="Evidence 详情">
      <header>
        <div><p className="eyebrow">{"\u53d7\u63a7\u8bc1\u636e / EVIDENCE"}</p><h2>{loading ? "读取中…" : evidence?.kind ?? "Evidence"}</h2></div>
        <button type="button" onClick={onClose} aria-label="关闭 Evidence 详情">×</button>
      </header>
      {evidence && (
        <div className="drawer-content">
          <p className="drawer-summary">{evidence.summary}</p>
          <dl>
            <div><dt>来源</dt><dd>{evidence.source_system}</dd></div>
            <div><dt>采集时间</dt><dd>{formatDate(evidence.collected_at)}</dd></div>
            <div><dt>截断</dt><dd>{evidence.truncated ? "是" : "否"}</dd></div>
          </dl>
          {isHttpUrl(evidence.source_uri) && (
            <a className="source-link" href={evidence.source_uri ?? undefined} target="_blank" rel="noreferrer">打开受控来源 ↗</a>
          )}
          <h3>查询参数</h3>
          <pre>{JSON.stringify(evidence.query, null, 2)}</pre>
          <h3>脱敏原始结果</h3>
          <pre>{JSON.stringify(evidence.raw_json ?? {}, null, 2)}</pre>
        </div>
      )}
    </aside>
  );
}

function isHttpUrl(value: string | null): boolean {
  return value?.startsWith("http://") === true || value?.startsWith("https://") === true;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "medium" }).format(new Date(value));
}
