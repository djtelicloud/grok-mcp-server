import { loadReceiptSigningConfig, receiptPublicJwk } from "../../lib/landing-receipt";

export const dynamic = "force-dynamic";

export function GET(): Response {
  try {
    return Response.json({ keys: [receiptPublicJwk(loadReceiptSigningConfig())] }, { headers: { "cache-control": "public, max-age=300", "access-control-allow-origin": "*" } });
  } catch {
    return Response.json({ keys: [] }, { status: 503, headers: { "cache-control": "no-store" } });
  }
}
