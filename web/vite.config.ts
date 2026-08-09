import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react()],
    server: {
      proxy: {
        "/api": environment.INCIDENTPILOT_WEB_API_URL ?? "http://127.0.0.1:8200",
      },
    },
  };
});
