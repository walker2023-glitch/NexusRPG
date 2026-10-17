/** @type {import('next').NextConfig} */
const nextConfig = {
  // Proxmox/LXC compatibility: use Babel instead of the native SWC compiler.
  swcMinify: false,
  output: 'standalone',   // produces .next/standalone for the Docker runner stage
  experimental: {
    // Proxmox/LXC homelab compatibility: avoid spawning a Webpack worker during build.
    // Next 14 enables this by default when no custom webpack config exists.
    webpackBuildWorker: false,
  },
}

export default nextConfig
