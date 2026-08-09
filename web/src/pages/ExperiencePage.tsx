import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { createApiClient } from "../api/client";

const operatorApi = createApiClient({ actorId: "local-operator" });

type EntryMode = "alert" | "service" | "file";
type ExecutionMode = "review" | "safe_auto" | "read_only";

const scenarios = [
  { id: "payment-unreachable-001", service: "checkout", label: "结算依赖不可达", note: "跨服务故障 · 四类遥测" },
  { id: "cart-failure-001", service: "cart", label: "购物车操作失败", note: "应用错误 · 指标与调用链" },
  { id: "recommendation-cache-leak-001", service: "recommendation", label: "推荐缓存异常", note: "缓存路径 · 真假设交叉验证" },
] as const;

export function ExperiencePage() {
  const navigate = useNavigate();
  const health = useQuery({ queryKey: ["demo-health"], queryFn: async () => {
    const response = await fetch("/demo-api/health/ready");
    if (!response.ok) throw new Error("演示运行器未就绪");
    return response.json() as Promise<{ ready: boolean }>;
  }, retry: false });

  return <main className="experience-page">
    <ExperienceLauncher
      runnerReady={health.data?.ready === true}
      onStart={async ({ title, description, service, scenarioId, controlled, executionMode }) => {
        const result = await operatorApi.createIncident({
          title, description, service, severity: "P1",
          starts_at: new Date().toISOString(), start_analysis: !controlled,
          execution_mode: executionMode,
        });
        if (controlled && scenarioId) await operatorApi.startDemoRun(result.incident.id, scenarioId);
        navigate(`/incidents/${result.incident.id}`);
      }}
    />
  </main>;
}

export function ExperienceLauncher({ onStart, runnerReady = true }: {
  runnerReady?: boolean;
  onStart: (input: { title: string; description: string; service: string; scenarioId?: string; controlled: boolean; executionMode: ExecutionMode }) => Promise<void>;
}) {
  const [mode, setMode] = useState<EntryMode>("alert");
  const [alert, setAlert] = useState("");
  const [service, setService] = useState("checkout");
  const [scenarioId, setScenarioId] = useState<(typeof scenarios)[number]["id"]>("payment-unreachable-001");
  const [fileName, setFileName] = useState<string>();
  const [executionMode, setExecutionMode] = useState<ExecutionMode>("review");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  async function start(controlled: boolean) {
    setBusy(true); setError(undefined);
    const scenario = scenarios.find((item) => item.id === scenarioId) ?? scenarios[0];
    try {
      await onStart(controlled ? {
        title: scenario.label,
        description: "由隔离演示运行器生成真实故障流量；诊断 Agent 不可访问场景答案。",
        service: scenario.service,
        scenarioId: scenario.id,
        controlled: true,
        executionMode,
      } : {
        title: alert.trim().split("\n")[0]?.slice(0, 120) || `${service} 服务告警`,
        description: alert.trim() || `用户选择 ${service} 服务并发起诊断。`,
        service,
        controlled: false,
        executionMode,
      });
    } catch (value) {
      setError(value instanceof Error ? value.message : "无法启动诊断");
      setBusy(false);
    }
  }

  return <>
    <section className="launch-hero">
      <div className="launch-copy">
        <span className="truth-chip"><i />真实微服务与遥测 · 非预设答案</span>
        <h1>AI 事故响应团队</h1>
        <p>把一条告警交给多 Agent 团队：它们会读取真实指标、日志与调用链，交叉验证根因，再把处置建议送入确定性安全门。</p>
        <div className="value-row"><span><strong>1.000</strong>多 Agent 验证得分</span><span><strong>0</strong>安全硬失败</span><span><strong>4</strong>专职调查角色</span></div>
      </div>
      <section className="launch-console" aria-label="发起诊断">
        <div className="console-head"><div><i /><span>AI 团队待命</span></div><small>{runnerReady ? "真实运行器在线" : "正在连接运行器"}</small></div>
        <div className="entry-tabs" role="tablist">
          {([ ["alert", "粘贴告警"], ["service", "选择服务"], ["file", "上传告警文件"] ] as const).map(([key, label]) =>
            <button type="button" role="tab" aria-selected={mode === key} className={mode === key ? "active" : ""} onClick={() => setMode(key)} key={key}>{label}</button>)}
        </div>
        {mode === "alert" && <label className="alert-input"><span>告警内容</span><textarea value={alert} onChange={(event) => setAlert(event.target.value)} placeholder="例如：checkout 错误率超过 5%，支付请求持续失败……" /></label>}
        {mode === "service" && <label className="alert-input"><span>需要调查的服务</span><select value={service} onChange={(event) => setService(event.target.value)}><option value="checkout">结算服务</option><option value="cart">购物车服务</option><option value="recommendation">推荐服务</option><option value="payment">支付服务</option></select><small>适合你已经知道异常服务，但没有完整告警文本的情况。</small></label>}
        {mode === "file" && <label className="file-input"><input type="file" accept=".json,.yaml,.yml,.txt,.log" onChange={async (event) => {
          const file = event.target.files?.[0]; if (!file) return;
          if (file.size > 512_000) { setError("文件不能超过 500 KB"); return; }
          setFileName(file.name); setAlert((await file.text()).slice(0, 4000));
        }} /><span>{fileName ?? "选择 JSON / YAML / TXT / LOG 文件"}</span><small>不支持截图 OCR；文件只作为本次告警上下文，不会执行其中内容。</small></label>}
        <fieldset className="execution-modes"><legend>处置策略 <span>默认策略</span></legend>{([
          ["review", "每次审批", "所有写操作都等你确认"],
          ["safe_auto", "安全托管", "仅低风险动作自动执行"],
          ["read_only", "仅诊断", "只调查，不修改系统"],
        ] as const).map(([value, title, copy]) => <label className={executionMode === value ? "active" : ""} key={value}><input type="radio" name="execution-mode" value={value} checked={executionMode === value} onChange={() => setExecutionMode(value)} /><strong>{title}</strong><small>{copy}</small></label>)}</fieldset>
        <button className="primary-launch" type="button" disabled={busy} onClick={() => void start(false)}>{busy ? "正在创建运行…" : "发起全新诊断"}<span>→</span></button>
        <div className="real-demo"><div><strong>没有告警？体验一次真实诊断</strong><small>每次都会创建新的后端运行，不是历史结果回放。</small></div><select aria-label="真实案例" value={scenarioId} onChange={(event) => setScenarioId(event.target.value as typeof scenarioId)}>{scenarios.map((item) => <option value={item.id} key={item.id}>{item.label}</option>)}</select><button type="button" disabled={busy || !runnerReady} onClick={() => void start(true)}>启动真实案例</button></div>
        {error && <p className="launch-error">{error}</p>}
      </section>
    </section>
    <section className="capability-strip" aria-label="系统能力">
      <article><span>01</span><strong>多 Agent 并行调查</strong><p>职责和工具严格隔离，不自由群聊。</p></article>
      <article><span>02</span><strong>有证据的根因</strong><p>结论必须引用实际 Evidence。</p></article>
      <article><span>03</span><strong>确定性安全控制</strong><p>权限与写操作不交给模型决定。</p></article>
      <article><span>04</span><strong>受控进化</strong><p>候选必须经过回归、影子评测和人工晋级。</p></article>
    </section>
  </>;
}
