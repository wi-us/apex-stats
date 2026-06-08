import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import tsConfigPaths from "vite-tsconfig-paths";
import { localAuthServerPlugin } from "./dev/local-auth-server";

export default defineConfig({
  plugins: [
    localAuthServerPlugin(),
    react(),
    tailwindcss(),
    tsConfigPaths(),
  ],
  server: {
    host: "0.0.0.0",
    port: 8080,
  },
  resolve: {
    alias: [
      {
        find: "@/lib/invites.functions",
        replacement: fileURLToPath(new URL("./src/lib/dev-server-functions.stub.ts", import.meta.url)),
      },
      {
        find: "@/lib/admin-users.functions",
        replacement: fileURLToPath(new URL("./src/lib/dev-server-functions.stub.ts", import.meta.url)),
      },
      {
        find: "@tanstack/react-start",
        replacement: fileURLToPath(new URL("./src/lib/dev-react-start.stub.ts", import.meta.url)),
      },
      {
        find: "@",
        replacement: fileURLToPath(new URL("./src", import.meta.url)),
      },
    ],
  },
  optimizeDeps: {
    noDiscovery: true,
    include: [
      "react",
      "react-dom/client",
      "react/jsx-dev-runtime",
      "react/jsx-runtime",
      "jszip",
      "use-sync-external-store/shim/with-selector",
    ],
  },
});
