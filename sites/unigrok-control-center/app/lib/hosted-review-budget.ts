/** Per-principal abuse budget for hosted review POSTs. */

const WINDOW_MS = 10 * 60 * 1000;
const MAX_PER_WINDOW = 5;
const MAX_IN_FLIGHT = 1;

type Bucket = {
  timestamps: number[];
  inFlight: number;
};

const buckets = new Map<string, Bucket>();

export type HostedReviewBudgetDecision =
  | { ok: true; release: () => void }
  | { ok: false; retryAfterSec: number };

/**
 * Acquire a short-lived review slot for `principal` (GitHub login).
 *
 * Limits: one in-flight review and five starts per rolling 10 minutes.
 * Process-local only — enough to blunt cookie-session abuse on a single
 * control-center isolate.
 */
export function tryAcquireHostedReviewBudget(
  principal: string,
  now = Date.now(),
): HostedReviewBudgetDecision {
  const key = principal.trim().toLowerCase();
  if (!key) return { ok: false, retryAfterSec: 60 };

  let bucket = buckets.get(key);
  if (!bucket) {
    bucket = { timestamps: [], inFlight: 0 };
    buckets.set(key, bucket);
  }

  bucket.timestamps = bucket.timestamps.filter((stamp) => now - stamp < WINDOW_MS);

  if (bucket.inFlight >= MAX_IN_FLIGHT) {
    return { ok: false, retryAfterSec: 30 };
  }
  if (bucket.timestamps.length >= MAX_PER_WINDOW) {
    const oldest = bucket.timestamps[0] ?? now;
    const retryAfterSec = Math.max(1, Math.ceil((WINDOW_MS - (now - oldest)) / 1000));
    return { ok: false, retryAfterSec };
  }

  bucket.inFlight += 1;
  bucket.timestamps.push(now);
  let released = false;
  return {
    ok: true,
    release: () => {
      if (released) return;
      released = true;
      const current = buckets.get(key);
      if (!current) return;
      current.inFlight = Math.max(0, current.inFlight - 1);
    },
  };
}

/** Test-only reset of process-local buckets. */
export function resetHostedReviewBudgetForTests(): void {
  buckets.clear();
}
