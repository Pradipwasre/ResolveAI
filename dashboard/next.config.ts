import type { NextConfig } from "next";

// Static export: the built dashboard is plain HTML/JS served by the FastAPI container.
const config: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default config;
