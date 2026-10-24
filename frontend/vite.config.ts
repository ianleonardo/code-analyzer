import { fileURLToPath } from "node:url";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Settings come from the project's root .env (shared with the backend); real environment variables win.
const rootDir = fileURLToPath(new URL("..", import.meta.url));

/** FRONTEND_ALLOWED_HOSTS: comma-separated host names; a leading dot allows subdomains; "*" allows any host. */
function allowedHosts(raw: string | undefined): string[] | true | undefined {
  const hosts = (raw ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  if (hosts.includes("*")) return true;
  return hosts.length ? hosts : undefined;
}

export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, rootDir, ""), ...process.env };
  const hosts = allowedHosts(env.FRONTEND_ALLOWED_HOSTS);
  return {
    plugins: [react()],
    optimizeDeps: { include: ["mermaid"] },
    server: {
      host: "0.0.0.0",
      port: Number(env.FRONTEND_PORT) || 7778,
      allowedHosts: hosts,
      fs: { allow: [rootDir] },
      proxy: { "/api": env.BACKEND_URL || "http://127.0.0.1:8001" },
    },
    build: { chunkSizeWarningLimit: 1500 },
    preview: { allowedHosts: hosts },
    test: { environment: "jsdom", setupFiles: "./src/test-setup.ts" },
  };
});
