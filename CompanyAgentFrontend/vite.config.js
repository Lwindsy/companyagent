import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api/python': {
        target: process.env.VITE_PYTHON_API_URL || 'http://localhost:8000',
        changeOrigin: true,
        headers: { 'X-Forwarded-Prefix': '/api/python' },
        rewrite: (path) => path.replace(/^\/api\/python/, '')
      },
      '/api/java': {
        target: process.env.VITE_JAVA_API_URL || 'http://localhost:8080',
        changeOrigin: true,
        headers: { 'X-Forwarded-Prefix': '/api/java' },
        rewrite: (path) => path.replace(/^\/api\/java/, '')
      },
      '/api/dotnet': {
        target: process.env.VITE_DOTNET_API_URL || 'http://localhost:8090',
        changeOrigin: true,
        headers: { 'X-Forwarded-Prefix': '/api/dotnet' },
        rewrite: (path) => path.replace(/^\/api\/dotnet/, '')
      }
    }
  }
})
