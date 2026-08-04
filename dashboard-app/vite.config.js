import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

/**
 * Builds into mt5-bridge/dashboard/app, which bridge.py already serves as
 * static files. No second web server, no CORS, no proxy in production — the
 * page is same-origin with the API it reads, exactly as the plain dashboard is.
 *
 * `base` is absolute because the page is served from /dashboard/app/ and the
 * asset URLs have to resolve from there rather than from the document path.
 */
export default defineConfig({
  plugins: [react()],
  base: '/dashboard/app/',
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: '../mt5-bridge/dashboard/app',
    emptyOutDir: true,
    // Charts are the heaviest dependency and most panels below the fold use
    // them; splitting keeps the first paint from waiting on Recharts.
    rollupOptions: {
      output: {
        manualChunks: {
          // React itself is not split out: the JSX runtime is imported by the
          // entry chunk anyway, so a separate chunk comes out empty.
          charts: ['recharts'],
          table: ['@tanstack/react-table', '@tanstack/react-virtual'],
          motion: ['framer-motion'],
        },
      },
    },
  },
  server: {
    // `npm run dev` talks to a bridge already running on 8765.
    proxy: {
      '/history': 'http://127.0.0.1:8765',
      '/overview': 'http://127.0.0.1:8765',
      '/trades': 'http://127.0.0.1:8765',
    },
  },
});
