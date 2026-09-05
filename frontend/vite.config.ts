import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // react-three-fiber resolves its own React copy otherwise, which throws
    // "Invalid hook call … more than one copy of React in the same app" and
    // blanks the canvas.
    dedupe: ["react", "react-dom"],
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  optimizeDeps: {
    include: ["react", "react-dom", "three"],
  },
  server: {
    port: Number(process.env.PORT) || 5173,
    proxy: {
      // The real, already-tested FastAPI backend (src/orderguard/app.py).
      // No backend changes for this frontend rewrite — same REST contract
      // the existing web/ UI already uses.
      "/api": "http://localhost:8000",
      "/mcp": "http://localhost:8000",
    },
  },
})
