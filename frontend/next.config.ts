import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The JavaScript API avoids child-process CLI parsing failures in constrained runtimes.
  experimental: {
    useTypeScriptCli: false,
  },
};

export default nextConfig;
