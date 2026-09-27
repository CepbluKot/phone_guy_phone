import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const adminUIRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  plugins: [react()],
  base: "/admin/",
  test: { environment: "jsdom", restoreMocks: true },
  build: { outDir: "dist", emptyOutDir: true, rollupOptions: { input: { admin: resolve(adminUIRoot, "index.html"), phone: resolve(adminUIRoot, "phone.html") } } },
});
