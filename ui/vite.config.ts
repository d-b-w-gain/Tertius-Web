import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { execSync } from 'child_process'

let commitHash = 'unknown';
try {
  commitHash = execSync('git rev-parse --short HEAD').toString().trim();
} catch (e) {
  console.warn('Could not read git commit hash');
}
// https://vite.dev/config/
export default defineConfig({
  define: {
    __COMMIT_HASH__: JSON.stringify(commitHash),
  },
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
