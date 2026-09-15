import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 开发期通过 Vite 代理访问后端,前端代码里不出现任何后端地址与密钥
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5174,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
