/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Standalone output so the container image carries only what it needs.
  output: "standalone",
  poweredByHeader: false,
  // TypeScript in strict mode with noUncheckedIndexedAccess is the real gate
  // here; it already caught two genuine nullability bugs. ESLint is not
  // installed, and a missing linter should not fail a production build.
  eslint: { ignoreDuringBuilds: true },
  env: {
    // Exposed to the client only as a URL; the admin credential stays on the
    // server and is never sent to the browser.
    NEXT_PUBLIC_WEBSOCKET_URL: process.env.NEXT_PUBLIC_WEBSOCKET_URL ?? "",
  },
};

export default nextConfig;
