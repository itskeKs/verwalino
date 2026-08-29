import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
  server: {
    port: 3000,
    proxy: {
      // Proxy /api requests to the FastAPI backend during development.
      // This avoids CORS issues entirely -- the browser only talks to
      // the Vite dev server; Vite forwards /api/* to the backend.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
