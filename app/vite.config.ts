import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri 开发模式固定使用 1420 端口（见 src-tauri/tauri.conf.json 的 devUrl）
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: { ignored: ["**/src-tauri/**"] },
  },
  envPrefix: ["VITE_", "TAURI_ENV_"],
  build: {
    target: "safari16",
    outDir: "dist",
  },
});
