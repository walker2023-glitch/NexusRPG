/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',   // produces .next/standalone for the Docker runner stage
  experimental: {
    // Server Actions are stable in Next 14; no flag needed — listed here for clarity
  },
}

export default nextConfig
