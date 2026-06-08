import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { execSync } from 'node:child_process'

function readGitValue(command: string, fallback: string) {
  try {
    return execSync(command, { encoding: 'utf8' }).trim() || fallback
  } catch {
    return fallback
  }
}

const gitCommit = readGitValue('git rev-parse --short HEAD', 'unknown')
const gitCommitDate = readGitValue('git log -1 --format=%cI', 'unknown')

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  define: {
    __GIT_COMMIT__: JSON.stringify(gitCommit),
    __GIT_COMMIT_DATE__: JSON.stringify(gitCommitDate),
  },
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
