import { expect, test } from "@playwright/test";

test("observes a real read-only incident from result through evidence", async ({ page, request }) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  const response = await request.get("http://127.0.0.1:8201/api/v1/incidents?limit=20", { headers: { "X-IncidentPilot-Actor": "local-viewer" } });
  const body = await response.json() as { items: Array<{ id: string; status: string }> };
  const incident = body.items.find((item) => item.status === "RESOLVED_READ_ONLY");
  expect(incident).toBeTruthy();
  await page.goto(`/incidents/${incident?.id}`);
  await expect(page.getByText("诊断结论", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "本次 AI 团队的调查轨迹" })).toBeVisible();
  await page.getByRole("button", { name: "专业调查详情" }).click();
  await expect(page.getByRole("heading", { name: "审计事件与 Evidence" })).toBeVisible();
  await expect(page.locator(".drawer-evidence article").first()).toBeVisible();

  expect(browserErrors).toEqual([]);
});
