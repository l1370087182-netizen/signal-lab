import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.SIGNAL_API_URL || process.env.SIGNAL_API_URL || 'http://127.0.0.1:9000'
  const webPort = Number(env.SIGNAL_WEB_PORT || process.env.SIGNAL_WEB_PORT || 5173)

  return {
    plugins: [react()],
    server: {
      port: webPort,
      strictPort: true,
      // Cloudflare / ngrok quick tunnels change host each time
      allowedHosts: true,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          // SSE: do not buffer event-stream
          timeout: 0,
          proxyTimeout: 0,
          configure: (proxy) => {
            proxy.on('proxyRes', (proxyRes, _req, res) => {
              const ct = String(proxyRes.headers['content-type'] || '')
              if (ct.includes('text/event-stream')) {
                res.setHeader('Cache-Control', 'no-cache, no-transform')
                res.setHeader('X-Accel-Buffering', 'no')
                delete proxyRes.headers['content-length']
                delete proxyRes.headers['content-encoding']
              }
            })
          },
        },
      },
    },
  }
})
