import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // This repo's root CLAUDE.md is the single source of truth for project
  // documentation (see its own "Repo layout" section) - don't let Next.js
  // auto-generate a second, conflicting CLAUDE.md/AGENTS.md pair here.
  agentRules: false,
  // Mirrors the old vite.config.ts dev proxy: the frontend only ever calls
  // relative /api/... paths (see src/lib/api/client.ts) and never needs a
  // hardcoded backend origin or CORS config - see CLAUDE.md's apps/web
  // section for why that property matters.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/:path*",
      },
    ];
  },
};

export default nextConfig;
