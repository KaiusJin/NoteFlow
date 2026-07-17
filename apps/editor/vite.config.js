import { defineConfig } from "vite";
import { resolve } from "node:path";

// Builds the editor into apps/web/vendor/editor as a self-contained ES module
// (plus one stylesheet). The static web app imports it at runtime with a
// dynamic import; no build step is required to *run* the app, only to
// regenerate this bundle after changing apps/editor.
export default defineConfig({
  define: {
    // Lib mode keeps `process.env.NODE_ENV` references from ProseMirror /
    // Milkdown intact; the browser has no `process`, so inline it.
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
  build: {
    lib: {
      entry: resolve(__dirname, "src/main.js"),
      formats: ["es"],
      fileName: () => "noteflow-editor.js",
    },
    outDir: resolve(__dirname, "../web/vendor/editor"),
    emptyOutDir: true,
    cssCodeSplit: false,
    chunkSizeWarningLimit: 4000,
  },
});
