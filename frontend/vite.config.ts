import process from "node:process";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
const backend = `http://127.0.0.1:${process.env.MEMORY_BACKEND_PORT || "8000"}`;
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": backend,
      "/metrics": backend,
      "/docs": backend,
      "/openapi.json": backend,
    },
  },
  build: { outDir: "dist" },
});
