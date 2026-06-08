import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: true,
    proxy: {
      '/proxy': {
        target: process.env.BACKEND_URL || 'http://backend:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/proxy/, ''),
        ws: true,
      },
      '/realms': {
        target: 'http://keycloak:8080',
        changeOrigin: false,
      },
      '/resources': {
        target: 'http://keycloak:8080',
        changeOrigin: false,
      },
    },
  },
})
