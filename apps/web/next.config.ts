import type { NextConfig } from "next";

const nextConfig: NextConfig = {

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
