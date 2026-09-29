import { createHash } from "node:crypto";
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

/** Emit dist/sw.js from sw.template.js with the precache list and a content version. */
function serviceWorker(): Plugin {
  let outDir = "dist";
  return {
    name: "cookt-sw",
    apply: "build",
    configResolved(config) {
      outDir = config.build.outDir;
    },
    closeBundle() {
      const files: string[] = [];
      const walk = (dir: string) => {
        for (const name of readdirSync(dir)) {
          const full = join(dir, name);
          if (statSync(full).isDirectory()) walk(full);
          else files.push("/" + relative(outDir, full).split("\\").join("/"));
        }
      };
      walk(outDir);
      const precache = files.filter((f) => !f.endsWith(".map") && f !== "/sw.js" && !f.startsWith("/screens") && !f.startsWith("/splash/"));
      const hash = createHash("sha256");
      for (const file of precache.sort()) hash.update(file).update(readFileSync(join(outDir, file)));
      const source = readFileSync("sw.template.js", "utf8")
        .replace("__VERSION__", hash.digest("hex").slice(0, 12))
        .replace("__PRECACHE__", JSON.stringify(precache.filter((f) => f !== "/index.html")));
      writeFileSync(join(outDir, "sw.js"), source);
    },
  };
}

export default defineConfig({
  plugins: [react(), serviceWorker()],
  build: { target: "es2022", sourcemap: false, assetsInlineLimit: 0 },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8088",
      "/images": "http://127.0.0.1:8088",
      "/mcp": "http://127.0.0.1:8088",
    },
  },
  test: { environment: "jsdom", include: ["src/**/*.test.ts", "src/**/*.test.tsx"] },
} as never);
