import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
export default defineConfig({
  plugins: [vue()],
  base: "./",
  server: { proxy: { "/api": "http://127.0.0.1:8891" } },
  build: {
    outDir: "../web/console",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return;
          if (id.includes("tdesign-icons")) return "icons";
          if (id.includes("tdesign-vue-next")) return "tdesign";
          if (id.includes("/vue/") || id.includes("/@vue/")) return "vue";
          if (id.includes("lodash")) return "utilities";
          return "vendor";
        },
      },
    },
  },
});
