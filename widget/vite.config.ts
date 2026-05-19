import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'

export default defineConfig({
  plugins: [preact()],
  resolve: {
    alias: {
      'react': 'preact/compat',
      'react-dom': 'preact/compat',
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: undefined,
        inlineDynamicImports: true,
      },
    },
    lib: {
      entry: 'src/widget.tsx',
      formats: ['iife'],
      name: 'MaintainerCopilot',
      fileName: () => 'widget.js',
    },
  },
})
