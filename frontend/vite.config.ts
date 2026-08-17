import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: the React app runs on 5173 and proxies /api to the FastAPI backend on 8766, so the
// frontend talks to a same-origin "/api" in both dev and production.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8766", changeOrigin: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
