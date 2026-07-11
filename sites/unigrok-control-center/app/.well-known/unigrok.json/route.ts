import { publicDiscoveryDocument } from "../../lib/public-project";

export const dynamic = "force-static";

export function GET() {
  return Response.json(publicDiscoveryDocument(), {
    headers: {
      "cache-control": "public, max-age=300, stale-while-revalidate=3600",
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
      "referrer-policy": "no-referrer",
      "x-content-type-options": "nosniff",
    },
  });
}
