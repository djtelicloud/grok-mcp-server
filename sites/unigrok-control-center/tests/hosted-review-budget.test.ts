import assert from "node:assert/strict";
import test from "node:test";
import {
  resetHostedReviewBudgetForTests,
  tryAcquireHostedReviewBudget,
} from "../app/lib/hosted-review-budget";

test("hosted review budget limits in-flight and rolling starts", () => {
  resetHostedReviewBudgetForTests();
  const t0 = 1_800_000_000_000;

  const first = tryAcquireHostedReviewBudget("alice", t0);
  assert.equal(first.ok, true);
  const blockedInFlight = tryAcquireHostedReviewBudget("alice", t0 + 1);
  assert.equal(blockedInFlight.ok, false);
  if (first.ok) first.release();

  for (let index = 0; index < 4; index += 1) {
    const grant = tryAcquireHostedReviewBudget("alice", t0 + 1_000 + index);
    assert.equal(grant.ok, true);
    if (grant.ok) grant.release();
  }

  const sixth = tryAcquireHostedReviewBudget("alice", t0 + 2_000);
  assert.equal(sixth.ok, false);
  if (!sixth.ok) assert.ok(sixth.retryAfterSec >= 1);

  const other = tryAcquireHostedReviewBudget("bob", t0 + 2_000);
  assert.equal(other.ok, true);
  if (other.ok) other.release();

  const afterWindow = tryAcquireHostedReviewBudget("alice", t0 + 10 * 60 * 1000 + 1);
  assert.equal(afterWindow.ok, true);
  if (afterWindow.ok) afterWindow.release();
});
