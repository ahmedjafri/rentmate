import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

const backendProxy = {
  '/graphql':      { target: 'http://127.0.0.1:8002', changeOrigin: true },
  '/auth':         { target: 'http://127.0.0.1:8002', changeOrigin: true },
  '/api':          { target: 'http://127.0.0.1:8002', changeOrigin: true },
  '/settings':     { target: 'http://127.0.0.1:8002', changeOrigin: true },
  '/chat':         { target: 'http://127.0.0.1:8002', changeOrigin: true },
  '/automations':  { target: 'http://127.0.0.1:8002', changeOrigin: true },
  '/action-items': { target: 'http://127.0.0.1:8002', changeOrigin: true },
  '/dev/':         { target: 'http://127.0.0.1:8002', changeOrigin: true },
  '/dialpad-webhook': { target: 'http://127.0.0.1:8002', changeOrigin: true },
};

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  server: {
    host: "::",
    port: 8080,
    proxy: backendProxy,
    hmr: {
      overlay: false,
    },
  },
  build: {
    outDir: 'dist',
  },
  plugins: [react(), mode === "development" && componentTagger()].filter(Boolean),
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
    dedupe: ["react", "react-dom", "react/jsx-runtime"],
  },
}));
