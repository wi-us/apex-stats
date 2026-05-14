import type { NextConfig } from "next";

const apiPort = process.env.NEXT_PUBLIC_API_PORT ?? "4000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
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
