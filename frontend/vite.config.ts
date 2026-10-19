import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const apiTarget =
  process.env.HV_API_TARGET || "http://127.0.0.1:18000";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 13000,
    proxy: {
      // Proxy /api requests to the FastAPI backend during development.
      // This avoids CORS issues entirely -- the browser only talks to
      // the Vite dev server; Vite forwards /api/* to the backend.
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    restoreMocks: true,
  },
  build: {
    // The reviewed SPA bundle is currently ~517 kB; retain a tight 550 kB budget.
    chunkSizeWarningLimit: 550,
  },
});
