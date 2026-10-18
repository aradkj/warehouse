import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/v1": {
        target: process.env.VITE_BACKEND_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/healthz": {
        target: process.env.VITE_BACKEND_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/readyz": {
        target: process.env.VITE_BACKEND_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  // @ts-expect-error vitest config in vite config
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    css: false,
  },
});
