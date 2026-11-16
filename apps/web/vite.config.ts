import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// M01 §4.1：dev 直连本机 control-plane（VITE_API_BASE）；full 模式由 compose 覆写
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_BASE ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: { outDir: "dist" },
});
