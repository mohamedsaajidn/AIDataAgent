import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // This repo's root CLAUDE.md is the single source of truth for project
  // documentation (see its own "Repo layout" section) - don't let Next.js
  // auto-generate a second, conflicting CLAUDE.md/AGENTS.md pair here.
  agentRules: false,
  experimental: {
    // Next.js's rewrite proxy hardcodes a 30 SECOND timeout
    // (proxyRequest's `proxyTimeout || 30000`, see node_modules/next/dist/
    // esm/server/lib/router-utils/proxy-request.js) when this isn't set -
    // confirmed live by hitting a deliberately slow backend route through
    // this exact proxy: it returned a bare "Internal Server Error" 500 at
    // ~30.0s while the backend kept running untouched in the background
    // (no exception, no log line - it simply wasn't asked yet). This is
    // exactly what a real agent turn hit: a multi-tool-round chat/EDA
    // question is genuinely a multi-LLM-call, multi-sandbox-execution
    // pipeline with its own bounded retries (agent.graphs.qa.MAX_ATTEMPTS,
    // orchestrator.MAX_TOOL_ROUNDS) - one real turn was measured at 111.7s
    // (a transform that needed a retry), and a multi-chart turn compounds
    // further (one make_chart call per requested chart, sequentially). The
    // user saw this as an intermittent "500 Internal Server Error" with
    // nothing in the backend's own log, followed by the real answer
    // already sitting there on refresh - exactly consistent with the
    // backend having finished fine after the proxy had already given up
    // and told the browser it failed. 300000ms (5 minutes) comfortably
    // covers a pathological worst case (multiple tool rounds, each
    // retried) without leaving a truly hung request open forever.
    proxyTimeout: 300000,
  },
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
