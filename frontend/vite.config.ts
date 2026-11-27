import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev server on 5600; /api is proxied to the local API (Compose publishes it on 8600).
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1', // plain `localhost` may bind only to IPv6 on Windows
    port: 5600,
    strictPort: true,
    proxy: {
      '/api': process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8600',
    },
  },
})
