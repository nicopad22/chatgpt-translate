import { defineConfig } from 'vite';

// A simple plugin to simulate vercel.json cleanUrls locally (app.html -> /app, etc.)
function cleanUrlsPlugin() {
  return {
    name: 'clean-urls',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = new URL(req.url, `http://${req.headers.host}`);
        let pathname = url.pathname;

        // Strip trailing slash if present
        if (pathname.length > 1 && pathname.endsWith('/')) {
          pathname = pathname.slice(0, -1);
        }

        // Clean URLs mapping (simulate cleanUrls: true)
        const cleanRoutes = {
          '/app': '/app.html',
          '/login': '/login.html',
          '/signup': '/signup.html',
          '/account': '/account.html'
        };

        if (cleanRoutes[pathname]) {
          req.url = cleanRoutes[pathname] + url.search;
        }

        next();
      });
    }
  };
}

export default defineConfig({
  plugins: [cleanUrlsPlugin()],
  server: {
    port: 5173
  }
});
