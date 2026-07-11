import assert from "node:assert/strict";
import test from "node:test";

const developmentPreviewMeta =
  /<meta(?=[^>]*\bname=["']codex-preview["'])(?=[^>]*\bcontent=["']development["'])[^>]*>/i;

async function loadWorker() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-${Math.random()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker;
}

function executionContext() {
  return {
    passThroughOnException() {},
    waitUntil() {},
  };
}

function workerEnvironment() {
  return {
    ASSETS: {
      fetch: async () => new Response("Not found", { status: 404 }),
    },
  };
}

function replaceEnvironment(values) {
  const previous = new Map();
  for (const [key, value] of Object.entries(values)) {
    previous.set(key, process.env[key]);
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  return () => {
    for (const [key, value] of previous) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  };
}

async function authenticatedResponse(worker) {
  return worker.fetch(
    new Request("http://localhost/", {
      headers: {
        accept: "text/html",
        "oai-authenticated-user-email": "installer@example.org",
      },
    }),
    workerEnvironment(),
    executionContext(),
  );
}

test("redirects anonymous visitors to dispatch-owned ChatGPT sign-in", async () => {
  const worker = await loadWorker();
  const response = await worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    workerEnvironment(),
    executionContext(),
  );

  assert.equal(response.status, 307);
  const location = new URL(response.headers.get("location"));
  assert.equal(location.origin, "http://localhost");
  assert.equal(location.pathname, "/signin-with-chatgpt");
  assert.equal(location.search, "?return_to=%2F");
});

test("renders the authenticated installer without serializing email", async () => {
  const worker = await loadWorker();
  const response = await worker.fetch(
    new Request("http://localhost/", {
      headers: {
        accept: "text/html",
        "oai-authenticated-user-email": "installer@example.org",
        "oai-authenticated-user-full-name": "Template%20Installer",
        "oai-authenticated-user-full-name-encoding": "percent-encoded-utf-8",
      },
    }),
    workerEnvironment(),
    executionContext(),
  );

  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, developmentPreviewMeta);
  assert.match(html, /Template Installer/);
  assert.match(html, /Pull-request status/);
  assert.match(html, /Grok review results/);
  assert.match(html, /Unverified from this Site/);
  assert.match(html, /PR data is not connected/);
  assert.doesNotMatch(html, /installer@example\.org/);
});

test("uses a neutral client label when ChatGPT supplies no full name", async () => {
  const worker = await loadWorker();
  const response = await worker.fetch(
    new Request("http://localhost/", {
      headers: {
        accept: "text/html",
        "oai-authenticated-user-email": "installer@example.org",
      },
    }),
    workerEnvironment(),
    executionContext(),
  );

  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /ChatGPT user/);
  assert.doesNotMatch(html, /installer@example\.org/);
});

test("ignores an untrusted full-name encoding", async () => {
  const worker = await loadWorker();
  const response = await worker.fetch(
    new Request("http://localhost/", {
      headers: {
        accept: "text/html",
        "oai-authenticated-user-email": "installer@example.org",
        "oai-authenticated-user-full-name": "Untrusted%20Name",
        "oai-authenticated-user-full-name-encoding": "plain-text",
      },
    }),
    workerEnvironment(),
    executionContext(),
  );

  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /ChatGPT user/);
  assert.doesNotMatch(html, /installer@example\.org|Untrusted Name/);
});

test("does not expose the development preview route in production", async () => {
  const worker = await loadWorker();
  const response = await worker.fetch(
    new Request("http://localhost/preview", { headers: { accept: "text/html" } }),
    workerEnvironment(),
    executionContext(),
  );

  assert.equal(response.status, 404);
});

test("renders nondefault loopback and tunnel metadata", async () => {
  let restore = replaceEnvironment({
    UNIGROK_CONNECTION_MODE: "local",
    UNIGROK_LOCAL_BASE_URL: "http://127.0.0.1:5876",
    UNIGROK_TUNNEL_PROFILE: "unigrok",
  });
  try {
    const response = await authenticatedResponse(await loadWorker());
    const html = await response.text();
    assert.match(html, /127\.0\.0\.1:5876/);
    assert.match(html, /Local development/);
  } finally {
    restore();
  }

  restore = replaceEnvironment({
    UNIGROK_CONNECTION_MODE: "tunnel",
    UNIGROK_LOCAL_BASE_URL: "http://127.0.0.1:4765",
    UNIGROK_TUNNEL_PROFILE: "team_profile-2",
  });
  try {
    const response = await authenticatedResponse(await loadWorker());
    const html = await response.text();
    assert.match(html, /Tunnel profile: team_profile-2/);
    assert.match(html, /Secure tunnel/);
  } finally {
    restore();
  }
});

test("rejects public local URLs and malformed environment metadata", async () => {
  const restore = replaceEnvironment({
    GITHUB_REPOSITORY: "invalid repository value",
    UNIGROK_CONNECTION_MODE: "local",
    UNIGROK_LOCAL_BASE_URL: "http://example.org:4765",
    UNIGROK_TUNNEL_PROFILE: "invalid profile value",
  });
  try {
    const response = await authenticatedResponse(await loadWorker());
    const html = await response.text();
    assert.match(html, /Setup needed/);
    assert.match(html, /Repository not configured/);
    assert.doesNotMatch(html, /example\.org:4765|invalid repository value|invalid profile value/);
  } finally {
    restore();
  }
});
