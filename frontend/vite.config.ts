import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "..", "");
  return {
    envDir: "..",
    plugins: [react()],
    server: {
      host: env.VITE_FRONTEND_HOST || "127.0.0.1",
      port: Number(env.VITE_FRONTEND_PORT || 5173),
    },
    preview: {
      host: env.VITE_FRONTEND_HOST || "127.0.0.1",
      port: Number(env.VITE_FRONTEND_PORT || 5173),
    },
  };
});
