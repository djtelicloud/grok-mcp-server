/** Process-local abuse budget for public OAuth dynamic client registration. */

const WINDOW_MS = 10 * 60 * 1000;
const MAX_PER_WINDOW = 20;

const buckets = new Map<string, number[]>();

export type OAuthRegisterBudgetDecision =
  | { ok: true }
  | { ok: false; retryAfterSec: number };

/**
 * Acquire a registration slot for a coarse client key (usually remote IP).
 *
 * Public `/oauth/register` is unauthenticated and CORS-open; this bound
 * blunts signing spam on a single isolate (20 starts / 10 minutes).
 */
export function tryAcquireOAuthRegisterBudget(
  clientKey: string,
  now = Date.now(),
): OAuthRegisterBudgetDecision {
  const key = clientKey.trim().toLowerCase().slice(0, 128) || "anon";
  const prior = buckets.get(key) ?? [];
  const timestamps = prior.filter((stamp) => now - stamp < WINDOW_MS);
  if (timestamps.length >= MAX_PER_WINDOW) {
    const oldest = timestamps[0] ?? now;
    const retryAfterSec = Math.max(1, Math.ceil((WINDOW_MS - (now - oldest)) / 1000));
    buckets.set(key, timestamps);
    return { ok: false, retryAfterSec };
  }
  timestamps.push(now);
  buckets.set(key, timestamps);
  return { ok: true };
}

/** Best-effort client key for registration budgets (not an auth principal). */
export function registrationClientKey(request: Request): string {
  const cf = request.headers.get("cf-connecting-ip")?.trim();
  if (cf && cf.length <= 64 && !/[\s,]/.test(cf)) return cf;
  const forwarded = request.headers.get("x-forwarded-for")?.split(",", 1)[0]?.trim();
  if (forwarded && forwarded.length <= 64 && !/\s/.test(forwarded)) return forwarded;
  return "anon";
}

/** Test-only reset of process-local buckets. */
export function resetOAuthRegisterBudgetForTests(): void {
  buckets.clear();
}
