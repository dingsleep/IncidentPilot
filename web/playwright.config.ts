import { defineConfig } from "@playwright/test";

const python = "D:\\software\\ana\\envs\\tx_agent\\python.exe";
const apiPort = process.env.INCIDENTPILOT_PLAYWRIGHT_API_PORT ?? "8200";
const webPort = process.env.INCIDENTPILOT_PLAYWRIGHT_WEB_PORT ?? "5173";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: "line",
  outputDir: "../.runtime/playwright",
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    browserName: "chromium",
    channel: "msedge",
    headless: true,
    viewport: { width: 1440, height: 960 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: `"${python}" -m uvicorn incidentpilot.api.main:app --host 127.0.0.1 --port ${apiPort}`,
      env: {
        INCIDENTPILOT_ENV: "development",
        INCIDENTPILOT_API_DATABASE_URL:
          "postgresql+asyncpg://incident_api_role:api-local-only@127.0.0.1:5433/incidentpilot",
      },
      url: `http://127.0.0.1:${apiPort}/api/v1/health/live`,
      timeout: 30_000,
      reuseExistingServer: false,
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${webPort}`,
      env: {
        INCIDENTPILOT_WEB_API_URL: `http://127.0.0.1:${apiPort}`,
      },
      url: `http://127.0.0.1:${webPort}`,
      timeout: 30_000,
      reuseExistingServer: false,
    },
  ],
});
