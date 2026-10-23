/** Static export: `npm run build` writes ./out, which the PRAMAAN server serves directly (no Node in production,
 *  works air-gapped). During development run the API on :8000 and `NEXT_PUBLIC_API=http://localhost:8000 npm run dev`. */
const nextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
};
export default nextConfig;
