import type { NextConfig } from "next";

const apiPort = process.env.NEXT_PUBLIC_API_PORT ?? "4000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Default 10MB truncates video uploads proxied via /api → causes 500 on video-jobs.
  experimental: {
    proxyClientMaxBodySize: "4gb",
    middlewareClientMaxBodySize: "4gb",
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `http://127.0.0.1:${apiPort}/:path*`,
      },
    ];
  },
};

export default nextConfig;
