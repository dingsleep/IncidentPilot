import { GraphChart } from "echarts/charts";
import { TooltipComponent } from "echarts/components";
import { init, use } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

use([GraphChart, TooltipComponent, CanvasRenderer]);

export interface TopologyNode {
  name: string;
  role: "symptom" | "root" | "dependency" | "observed";
}

export interface TopologyLink {
  source: string;
  target: string;
}

export function ServiceTopology({ nodes, links }: { nodes: TopologyNode[]; links: TopologyLink[] }) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (container.current === null || nodes.length < 2) return;
    const chart = init(container.current, undefined, { renderer: "canvas" });
    chart.setOption({
      tooltip: { trigger: "item", formatter: "{b}" },
      series: [{
        type: "graph",
        layout: "force",
        roam: true,
        force: { repulsion: 260, edgeLength: 105 },
        data: nodes.map((node) => ({
          id: node.name,
          name: node.name,
          symbolSize: node.role === "root" ? 62 : 46,
          itemStyle: { color: color[node.role], borderColor: "#101413", borderWidth: 3 },
          label: { show: true, color: "#e8eee9", fontFamily: "Cascadia Code", fontSize: 11 },
        })),
        links: links.map((link) => ({ ...link, lineStyle: { color: "#56615d", width: 2 } })),
        lineStyle: { curveness: 0.08 },
      }],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [links, nodes]);

  if (nodes.length < 2) {
    return (
      <div className="topology-fallback">
        <strong>拓扑信号不足</strong>
        <p>等待 Trace 或 Diagnosis 提供第二个服务节点。</p>
        <TopologyTable nodes={nodes} />
      </div>
    );
  }
  return (
    <div className="topology-frame">
      <div ref={container} className="topology-chart" aria-label="服务拓扑图" />
      <details>
        <summary>表格视图</summary>
        <TopologyTable nodes={nodes} />
      </details>
    </div>
  );
}

const color: Record<TopologyNode["role"], string> = {
  symptom: "#f2b544",
  root: "#ff665c",
  dependency: "#63d5c5",
  observed: "#68736f",
};

function TopologyTable({ nodes }: { nodes: TopologyNode[] }) {
  return (
    <table className="topology-table">
      <thead><tr><th>服务</th><th>角色</th></tr></thead>
      <tbody>{nodes.map((node) => <tr key={node.name}><td>{node.name}</td><td>{node.role}</td></tr>)}</tbody>
    </table>
  );
}
