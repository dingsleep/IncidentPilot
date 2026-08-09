import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ExperienceLauncher } from "./ExperiencePage";

describe("ExperienceLauncher", () => {
  it("makes a fresh real backend diagnosis the primary experience", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter><ExperienceLauncher onStart={async () => undefined} /></MemoryRouter>,
    );

    expect(html).toContain("AI 事故响应团队");
    expect(html).toContain("体验一次真实诊断");
    expect(html).toContain("每次都会创建新的后端运行");
    expect(html).toContain("粘贴告警");
    expect(html).toContain("选择服务");
    expect(html).toContain("上传告警文件");
    expect(html).toContain("真实微服务与遥测");
    expect(html).toContain("每次审批");
    expect(html).toContain("安全托管");
    expect(html).toContain("仅诊断");
    expect(html).toContain("默认策略");
    expect(html).not.toContain("Mock");
  });
});
