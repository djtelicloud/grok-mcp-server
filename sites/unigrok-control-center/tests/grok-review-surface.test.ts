import assert from "node:assert/strict";
import test from "node:test";
import {
  deriveGrokReviewSurface,
  isUnigrokReviewCheckName,
  mcpOAuthConfigured,
} from "../app/lib/grok-review-surface";

test("isUnigrokReviewCheckName matches workflow job titles only", () => {
  assert.equal(isUnigrokReviewCheckName("UniGrok review"), true);
  assert.equal(isUnigrokReviewCheckName("UniGrok PR Review"), true);
  assert.equal(isUnigrokReviewCheckName("build (3.12)"), false);
  assert.equal(isUnigrokReviewCheckName("CodeQL"), false);
});

test("deriveGrokReviewSurface stays unconfigured without oauth or checks", () => {
  const surface = deriveGrokReviewSurface({ oauthConfigured: false, reviewChecks: [] });
  assert.equal(surface.state, "unconfigured");
  assert.equal(surface.score, null);
  assert.equal(surface.findings.length, 0);
  assert.equal(surface.verdict, null);
  assert.match(surface.message, /not connected/i);
});

test("deriveGrokReviewSurface reports awaiting when oauth is configured", () => {
  const surface = deriveGrokReviewSurface({ oauthConfigured: true, reviewChecks: [] });
  assert.equal(surface.state, "ready");
  assert.equal(surface.score, null);
  assert.equal(surface.verdict, "Awaiting review");
  assert.match(surface.message, /adapter is configured/i);
});

test("deriveGrokReviewSurface uses successful check evidence without inventing a score", () => {
  const surface = deriveGrokReviewSurface({
    oauthConfigured: false,
    reviewChecks: [
      {
        conclusion: "success",
        name: "UniGrok review",
        pullNumber: 114,
        status: "completed",
      },
    ],
  });
  assert.equal(surface.state, "ready");
  assert.equal(surface.score, null);
  assert.equal(surface.verdict, "Check passed");
  assert.match(surface.message, /PR #114/);
});

test("deriveGrokReviewSurface surfaces failed checks as error without a score", () => {
  const surface = deriveGrokReviewSurface({
    oauthConfigured: true,
    reviewChecks: [
      {
        conclusion: "failure",
        name: "UniGrok review",
        pullNumber: 76,
        status: "completed",
      },
    ],
  });
  assert.equal(surface.state, "error");
  assert.equal(surface.score, null);
  assert.equal(surface.verdict, null);
  assert.match(surface.message, /failure/);
});

test("mcpOAuthConfigured is injectable and fail-closed", () => {
  assert.equal(
    mcpOAuthConfigured(() => {
      throw new Error("missing");
    }),
    false,
  );
  assert.equal(
    mcpOAuthConfigured(() => ({ ok: true })),
    true,
  );
});
