import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'

export default defineConfig({
  plugins: [preact()],
  resolve: {
    alias: {
      react: 'preact/compat',
      'react-dom': 'preact/compat',
    },
  },
  build: {
    target: 'es2019',
    minify: 'terser',
    terserOptions: {
      compress: { passes: 2 },
      mangle: true,
    },
    chunkSizeWarningLimit: 50,
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
      // The bundle is served by the `widget` nginx container as
      // /widget-bundle.js; the public loader served by FastAPI lives at
      // /widget.js so the two never collide.
      fileName: () => 'widget-bundle.js',
    },
  },
})
