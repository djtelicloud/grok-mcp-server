import { publicProjectDocument } from "../../../../lib/public-project";

export const dynamic = "force-static";

export function GET() {
  return Response.json(publicProjectDocument(), {
    headers: {
      "cache-control": "public, max-age=300, stale-while-revalidate=3600",
      "content-type": "application/json; charset=utf-8",
    },
  });
}
