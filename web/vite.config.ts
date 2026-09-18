/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// In production the API serves index.html and fills these per request (for link previews).
// In dev, Vite serves index.html itself, so fill them with defaults.
function devPlaceholders(): Plugin {
  return {
    name: "dev-placeholders",
    apply: "serve",
    transformIndexHtml(html) {
      return html
        .replaceAll("__TITLE__", "SpeedStats")
        .replaceAll("__DESCRIPTION__", "speedrun.com rankings by run value")
        .replaceAll("__URL__", "http://localhost:5173/");
    },
  };
}

export default defineConfig({
  plugins: [react(), devPlaceholders()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
  build: { sourcemap: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    css: false,
  },
});
