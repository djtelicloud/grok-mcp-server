import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

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
      fetch: async (request) => {
        const pathname = new URL(request.url).pathname;
        if (pathname === "/.well-known/unigrok.json") {
          return new Response(
            await readFile(new URL("../public/.well-known/unigrok.json", import.meta.url), "utf8"),
            { headers: { "content-type": "application/json; charset=utf-8" } },
          );
        }
        if (pathname.startsWith("/docs/okf/")) {
          const fileName = pathname.slice("/docs/okf/".length);
          const file = new URL(`../public/docs/okf/${fileName}`, import.meta.url);
          try {
            return new Response(await readFile(file, "utf8"), {
              headers: {
                "content-type": fileName.endsWith(".json")
                  ? "application/json; charset=utf-8"
                  : "text/markdown; charset=utf-8",
              },
            });
          } catch {
            return new Response("Not found", { status: 404 });
          }
        }
        return new Response("Not found", { status: 404 });
      },
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

const ownerBinding = JSON.stringify([
  {
    chatgpt_email: "installer@example.org",
    github_login: "installer-github",
    role: "admin",
  },
]);

async function request(worker, path, headers = {}) {
  return worker.fetch(
    new Request(`http://localhost${path}`, { headers }),
    workerEnvironment(),
    executionContext(),
  );
}

async function authenticatedControlResponse(worker, headers = {}) {
  const restore = replaceEnvironment({
    UNIGROK_GITHUB_IDENTITY_BINDINGS: ownerBinding,
  });
  try {
    return await request(worker, "/control", {
      accept: "text/html",
      "oai-authenticated-user-email": "installer@example.org",
      ...headers,
    });
  } finally {
    restore();
  }
}

test("renders the public root without authentication or live-status claims", async () => {
  const response = await request(await loadWorker(), "/", { accept: "text/html" });

  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /One Grok gateway/);
  assert.match(html, /Public project context/);
  assert.match(html, /example · local command session/);
  assert.match(html, /Published route contract · not a live runtime probe/);
  assert.match(html, /Private OAuth · API plane only/);
  assert.match(html, /Swarm Playground/);
  assert.match(html, /href="\/swarm\/"/);
  assert.match(html, /uv run python main\.py init/);
  assert.match(html, /\/docs\/okf\/index\.md/);
  assert.match(html, /status.*healthy/);
  assert.match(html, /https:\/\/grokmcp\.org\/og\.png/);
  assert.doesNotMatch(html, /installer@example\.org/);
});

test("serves the canonical OKF manifest and generated API reference publicly", async () => {
  const worker = await loadWorker();
  const manifestResponse = await request(worker, "/docs/okf/okf-manifest.json");
  const apiResponse = await request(worker, "/docs/okf/api-reference.md");

  assert.equal(manifestResponse.status, 200);
  assert.equal(apiResponse.status, 200);
  const manifest = await manifestResponse.json();
  assert.ok(manifest.files.includes("api-reference.md"));
  assert.match(await apiResponse.text(), /async def agent\(/);
});

test("redirects anonymous control visitors to dispatch-owned ChatGPT sign-in", async () => {
  const response = await request(await loadWorker(), "/control", { accept: "text/html" });

  assert.equal(response.status, 307);
  const location = new URL(response.headers.get("location"));
  assert.equal(location.origin, "http://localhost");
  assert.equal(location.pathname, "/signin-with-chatgpt");
  assert.equal(location.search, "?return_to=%2Fcontrol");
});

test("denies a signed-in viewer when project authorization is unconfigured", async () => {
  const restore = replaceEnvironment({ UNIGROK_GITHUB_IDENTITY_BINDINGS: undefined });
  try {
    const response = await request(await loadWorker(), "/control", {
      accept: "text/html",
      "oai-authenticated-user-email": "installer@example.org",
      "oai-authenticated-user-full-name": "Template%20Installer",
      "oai-authenticated-user-full-name-encoding": "percent-encoded-utf-8",
    });
    assert.equal(response.status, 200);
    const html = await response.text();
    assert.match(html, /The control center is locked/);
    assert.match(html, /adapter has not been configured/);
    assert.match(html, /No control-center data was disclosed/);
    assert.doesNotMatch(html, /Pull-request status|installer@example\.org/);
  } finally {
    restore();
  }
});

test("denies malformed authorization configuration", async () => {
  const restore = replaceEnvironment({ UNIGROK_GITHUB_IDENTITY_BINDINGS: "not-json" });
  try {
    const response = await request(await loadWorker(), "/control", {
      accept: "text/html",
      "oai-authenticated-user-email": "installer@example.org",
    });
    const html = await response.text();
    assert.match(html, /configuration could not be validated/);
    assert.doesNotMatch(html, /Pull-request status|installer@example\.org/);
  } finally {
    restore();
  }
});

test("renders authorized control without serializing the ChatGPT email", async () => {
  const response = await authenticatedControlResponse(await loadWorker(), {
    "oai-authenticated-user-full-name": "Project%20Owner",
    "oai-authenticated-user-full-name-encoding": "percent-encoded-utf-8",
  });

  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Project Owner/);
  assert.match(html, /installer-github/);
  assert.match(html, /Pull-request status/);
  assert.match(html, /Grok review results/);
  assert.match(html, /canonical control origin performs live GitHub OAuth/);
  assert.doesNotMatch(html, /installer@example\.org/);
});

test("uses a neutral client label when ChatGPT supplies no full name", async () => {
  const response = await authenticatedControlResponse(await loadWorker());
  const html = await response.text();
  assert.match(html, /ChatGPT user/);
  assert.doesNotMatch(html, /installer@example\.org/);
});

test("ignores an untrusted full-name encoding", async () => {
  const response = await authenticatedControlResponse(await loadWorker(), {
    "oai-authenticated-user-full-name": "Untrusted%20Name",
    "oai-authenticated-user-full-name-encoding": "plain-text",
  });
  const html = await response.text();
  assert.match(html, /ChatGPT user/);
  assert.doesNotMatch(html, /installer@example\.org|Untrusted Name/);
});

test("does not expose the development preview route in production", async () => {
  const response = await request(await loadWorker(), "/preview", { accept: "text/html" });
  assert.equal(response.status, 404);
});

test("renders nondefault loopback and tunnel metadata for an authorized viewer", async () => {
  let restore = replaceEnvironment({
    UNIGROK_CONNECTION_MODE: "local",
    UNIGROK_LOCAL_BASE_URL: "http://127.0.0.1:5876",
    UNIGROK_TUNNEL_PROFILE: "unigrok",
  });
  try {
    const response = await authenticatedControlResponse(await loadWorker());
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
    const response = await authenticatedControlResponse(await loadWorker());
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
    const response = await authenticatedControlResponse(await loadWorker());
    const html = await response.text();
    assert.match(html, /Setup needed/);
    assert.match(html, /Repository not configured/);
    assert.doesNotMatch(html, /example\.org:4765|invalid repository value|invalid profile value/);
  } finally {
    restore();
  }
});

test("serves public project, discovery, and llms documents anonymously", async () => {
  const worker = await loadWorker();

  const projectResponse = await request(worker, "/api/public/v1/project");
  assert.equal(projectResponse.status, 200);
  const project = await projectResponse.json();
  assert.equal(project.name, "UniGrok");
  assert.equal(project.mcp.remote_status, "private-oauth-api-plane");
  assert.equal(project.mcp.private_remote, "https://mcp.grokmcp.org/mcp");
  assert.equal(project.control.authorization, "fresh-server-side-github-repository-role-check");
  assert.equal(project.documentation.okf_manifest, "https://grokmcp.org/docs/okf/okf-manifest.json");

  const discoveryResponse = await request(worker, "/.well-known/unigrok.json");
  assert.equal(discoveryResponse.status, 200);
  const discovery = await discoveryResponse.json();
  assert.equal(discovery.name, "UniGrok");
  assert.equal(discovery.control, "https://control.grokmcp.org");
  assert.equal(discovery.private_mcp, "https://mcp.grokmcp.org/mcp");
  assert.equal(discovery.okf, "https://grokmcp.org/docs/okf/okf-manifest.json");

  const llmsResponse = await request(worker, "/llms.txt");
  assert.equal(llmsResponse.status, 200);
  assert.match(llmsResponse.headers.get("content-type") ?? "", /^text\/plain\b/i);
  const llms = await llmsResponse.text();
  assert.match(llms, /# UniGrok/);
  assert.match(llms, /fresh server-side repository role check/);
  assert.match(llms, /OKF knowledge bundle/);
  assert.match(llms, /short-lived scoped tokens/);
  assert.doesNotMatch(llms, /xai-[A-Za-z0-9_-]+/i);
});
