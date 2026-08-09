import { defineConfig, loadEnv } from 'vite'
import path from 'path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'

import { resolveAgentOpsApiTransport } from './api-transport.mjs'

function figmaAssetResolver() {
  return {
    name: 'figma-asset-resolver',
    resolveId(id) {
      if (id.startsWith('figma:asset/')) {
        const filename = id.replace('figma:asset/', '')
        return path.resolve(__dirname, 'src/assets', filename)
      }
    },
  }
}

export default defineConfig(({ mode }) => {
  const environment = {
    ...loadEnv(mode, process.cwd(), 'VITE_AGENTOPS_'),
    ...process.env,
  }
  const transport = resolveAgentOpsApiTransport(environment)

  return {
    plugins: [
      figmaAssetResolver(),
      // The React and Tailwind plugins are both required for Make, even if
      // Tailwind is not being actively used – do not remove them
      react(),
      tailwindcss(),
    ],
    define: {
      __AGENTOPS_API_BASE__: JSON.stringify(transport.apiBase),
      __AGENTOPS_HUMAN_SESSION_REQUIRED__: JSON.stringify(
        transport.humanSessionRequired,
      ),
    },
    resolve: {
      alias: {
        // Alias @ to the src directory
        '@': path.resolve(__dirname, './src'),
        '@agentops-human-auth-local': path.resolve(
          __dirname,
          transport.humanSessionRequired
            ? './src/app/data/humanAuthCommercialUnavailable.ts'
            : './src/app/data/humanAuthLocalApi.ts',
        ),
      },
    },
    server: {
      proxy: transport.pythonProxyEnabled
        ? {
            '/mis-api': {
              target: process.env.VITE_AGENTOPS_PROXY_TARGET || 'http://127.0.0.1:8787',
              changeOrigin: true,
              rewrite: (path) => path.replace(/^\/mis-api/, '/api'),
            },
          }
        : {},
    },

    // File types to support raw imports. Never add .css, .tsx, or .ts files to this.
    assetsInclude: ['**/*.svg', '**/*.csv'],
  }
})
