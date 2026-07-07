import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev
export default defineConfig({
    plugins: [react()],
    base: './', // Ensures assets are loaded correctly via file:// protocol in Electron
    server: {
        port: parseInt(process.env.VITE_PORT || '3000', 10)
    }
});
