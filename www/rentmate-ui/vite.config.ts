import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const backendTarget = env.VITE_BACKEND_URL || 'http://127.0.0.1:8002';

  const backendProxy = {
    '/graphql':      { target: backendTarget, changeOrigin: true },
    '/auth':         { target: backendTarget, changeOrigin: true },
    '/api':          { target: backendTarget, changeOrigin: true },
    '/chat/':        { target: backendTarget, changeOrigin: true },
    '/automations':  { target: backendTarget, changeOrigin: true },
    '/action-items': { target: backendTarget, changeOrigin: true },
    '/dev/':         { target: backendTarget, changeOrigin: true },
    '/quo-webhook':  { target: backendTarget, changeOrigin: true },
    '/onboarding':   { target: backendTarget, changeOrigin: true },
  };

  return {
    server: {
      host: "::",
      port: 8080,
      allowedHosts: [
        "localhost",
        "127.0.0.1",
        "www.rentmate.orb.local",
        "www.rentmate"
      ],
      proxy: backendProxy,
      hmr: {
        overlay: false,
        // Pin the HMR websocket port so the client reconnects correctly when
        // the dev server is reached through OrbStack hostnames (where the
        // inferred port would otherwise be 80/443).
        clientPort: 8080,
      },
      // Polling is required because Vite runs inside a container and the
      // source tree is a Docker bind-mount. inotify events don't propagate
      // across bind mounts on Linux, so chokidar has to scan for changes.
      watch: {
        usePolling: true,
        interval: 200,
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
  };
});
