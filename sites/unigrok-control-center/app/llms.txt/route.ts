import { publicLlmsText } from "../lib/public-project";

export const dynamic = "force-static";

export function GET() {
  return new Response(publicLlmsText(), {
    headers: {
      "cache-control": "public, max-age=300, stale-while-revalidate=3600",
      "content-type": "text/plain; charset=utf-8",
    },
  });
}
