import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` serves the UI on http://localhost:5173 and forwards /api to the
// gateway in the local cluster. In the cluster, nginx serves the built files and
// Traefik routes /api to the gateway on the same origin, so no CORS is needed.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://app.localtest.me", changeOrigin: true, cookieDomainRewrite: "localhost" },
    },
  },
});
