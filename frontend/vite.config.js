import { defineConfig } from 'vite';
import fs from 'fs';
import path from 'path';

// A simple plugin to simulate vercel.json rewrites and cleanUrls locally
function localRewritesPlugin() {
  return {
    name: 'local-rewrites',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = new URL(req.url, `http://${req.headers.host}`);
        let pathname = url.pathname;

        // Strip trailing slash if present
        if (pathname.length > 1 && pathname.endsWith('/')) {
          pathname = pathname.slice(0, -1);
        }

        const rewrites = {
          '/': '/landing.html',
          '/app': '/index.html',
          '/login': '/login.html',
          '/signup': '/signup.html',
          '/account': '/account.html'
        };

        if (rewrites[pathname]) {
          req.url = rewrites[pathname] + url.search;
        }

        next();
      });
    }
  };
}

export default defineConfig({
  plugins: [localRewritesPlugin()],
  server: {
    port: 5173
  }
});
