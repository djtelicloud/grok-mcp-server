import assert from "node:assert/strict";
import test from "node:test";
import {
  registrationClientKey,
  resetOAuthRegisterBudgetForTests,
  tryAcquireOAuthRegisterBudget,
} from "../app/lib/oauth-register-budget";

test("oauth register budget limits rolling starts per client key", () => {
  resetOAuthRegisterBudgetForTests();
  const t0 = 1_800_000_000_000;
  for (let index = 0; index < 20; index += 1) {
    assert.equal(tryAcquireOAuthRegisterBudget("203.0.113.9", t0 + index).ok, true);
  }
  const blocked = tryAcquireOAuthRegisterBudget("203.0.113.9", t0 + 21);
  assert.equal(blocked.ok, false);
  if (!blocked.ok) assert.ok(blocked.retryAfterSec >= 1);

  assert.equal(tryAcquireOAuthRegisterBudget("198.51.100.2", t0 + 21).ok, true);
  assert.equal(
    tryAcquireOAuthRegisterBudget("203.0.113.9", t0 + 10 * 60 * 1000 + 1).ok,
    true,
  );
});

test("registrationClientKey prefers CF-Connecting-IP", () => {
  const request = new Request("https://control.example/oauth/register", {
    headers: {
      "cf-connecting-ip": "203.0.113.9",
      "x-forwarded-for": "198.51.100.2, 203.0.113.9",
    },
  });
  assert.equal(registrationClientKey(request), "203.0.113.9");
  assert.equal(
    registrationClientKey(new Request("https://control.example/oauth/register")),
    "anon",
  );
});
