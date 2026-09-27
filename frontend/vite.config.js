import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server proxies /api to the FastAPI backend so the browser only ever
// talks to one origin (no CORS surprises, works behind the preview proxy too).
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: false,
    // Allow any hostname (tunnels / reverse proxies / container previews).
    // Set to an explicit allow-list in production if you need tighter control.
    allowedHosts: true,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
      '/health': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  preview: { host: '0.0.0.0', port: 4173 },
  build: { outDir: 'dist', sourcemap: true },
})
