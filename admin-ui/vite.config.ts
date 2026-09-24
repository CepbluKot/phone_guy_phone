import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/admin/",
  test: { environment: "jsdom", restoreMocks: true },
  build: { outDir: "dist", emptyOutDir: true },
});
