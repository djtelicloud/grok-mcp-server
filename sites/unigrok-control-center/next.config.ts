import type { NextConfig } from "next";

const protectedHeaders = [
  { key: "Cache-Control", value: "private, no-store, max-age=0, must-revalidate" },
  { key: "Pragma", value: "no-cache" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  { key: "Content-Security-Policy", value: "base-uri 'none'; frame-ancestors 'none'; object-src 'none'" },
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
] as const;

const nextConfig: NextConfig =
  process.env.UNIGROK_BUILD_TARGET === "standalone"
    ? {
        experimental: { authInterrupts: true },
        headers: async () => [
          { headers: [...protectedHeaders], source: "/control/:path*" },
          { headers: [...protectedHeaders], source: "/auth/github/:path*" },
        ],
        output: "standalone",
        poweredByHeader: false,
      }
    : {};

export default nextConfig;
