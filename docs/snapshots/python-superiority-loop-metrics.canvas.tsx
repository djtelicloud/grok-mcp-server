/**
 * SNAPSHOT ONLY — repo history mirror of the IDE canvas.
 * Public share surface: docs/python-superiority-loop.md + .html
 * Live IDE canvas: ~/.cursor/projects/.../canvases/python-superiority-loop-metrics.canvas.tsx
 * Snapshot: 2026-07-16T17:12:45Z · 76/210 Ready plans (#342–#420)
 */
import {
  BarChart,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Divider,
  Grid,
  H1,
  H2,
  Link,
  Pill,
  Row,
  Select,
  Spacer,
  Stack,
  Stat,
  Table,
  Text,
  Toggle,
  UsageBar,
  useCanvasState,
  useHostTheme,
} from "cursor/canvas";

type FileMetric = {
  file: string;
  short: string;
  before: number;
  after: number;
  parseMs: number;
  compileMs: number;
  pr: number;
  status: "Ready" | "draft";
  loop: number;
};

/** Embedded from unigrok-intelligence tracker · 2026-07-16T17:12:45Z */
const FILES: FileMetric[] = [
  {
    file: "src/utils.py",
    short: "utils.py",
    before: 13830,
    after: 800,
    parseMs: 53,
    compileMs: 42,
    pr: 342,
    status: "Ready",
    loop: 1,
  },
  {
    file: "tests/test_utils.py",
    short: "test_utils.py",
    before: 7848,
    after: 200,
    parseMs: 29,
    compileMs: 26,
    pr: 345,
    status: "Ready",
    loop: 2,
  },
  {
    file: "tests/test_provider_broker.py",
    short: "test_provider_broker.py",
    before: 4707,
    after: 150,
    parseMs: 17,
    compileMs: 14,
    pr: 346,
    status: "Ready",
    loop: 3,
  },
  {
    file: "src/providers/broker.py",
    short: "broker.py",
    before: 3122,
    after: 600,
    parseMs: 9,
    compileMs: 8,
    pr: 347,
    status: "Ready",
    loop: 4,
  },
  {
    file: "src/http_server.py",
    short: "http_server.py",
    before: 2874,
    after: 400,
    parseMs: 11,
    compileMs: 9,
    pr: 348,
    status: "Ready",
    loop: 5,
  },
  {
    file: "tests/test_http_server.py",
    short: "test_http_server.py",
    before: 2371,
    after: 150,
    parseMs: 10,
    compileMs: 7,
    pr: 349,
    status: "Ready",
    loop: 6,
  },
  {
    file: "src/intelligence_payloads.py",
    short: "intelligence_payloads.py",
    before: 1749,
    after: 120,
    parseMs: 8,
    compileMs: 5,
    pr: 350,
    status: "Ready",
    loop: 7,
  },
  {
    file: "evals/.../stage1_harness.py",
    short: "stage1_harness.py",
    before: 1644,
    after: 200,
    parseMs: 5,
    compileMs: 5,
    pr: 351,
    status: "Ready",
    loop: 8,
  },
  {
    file: "src/tools/system.py",
    short: "system.py",
    before: 1525,
    after: 120,
    parseMs: 6,
    compileMs: 5,
    pr: 352,
    status: "Ready",
    loop: 9,
  },
  {
    file: "src/providers/subscription.py",
    short: "subscription.py",
    before: 1427,
    after: 150,
    parseMs: 5,
    compileMs: 3,
    pr: 353,
    status: "Ready",
    loop: 10,
  },
  {
    file: "tests/test_mcp_session_guard.py",
    short: "test_mcp_session_guard.py",
    before: 1397,
    after: 120,
    parseMs: 6,
    compileMs: 5,
    pr: 354,
    status: "Ready",
    loop: 11,
  },
  {
    file: "tests/test_mcp_sampling_bridge.py",
    short: "test_mcp_sampling_bridge.py",
    before: 1379,
    after: 120,
    parseMs: 5,
    compileMs: 4,
    pr: 355,
    status: "Ready",
    loop: 12,
  },
  {
    file: "evals/.../attempt_ledger.py",
    short: "attempt_ledger.py",
    before: 1298,
    after: 200,
    parseMs: 4,
    compileMs: 4,
    pr: 356,
    status: "Ready",
    loop: 13,
  },
  {
    file: "tests/test_intelligence_payloads.py",
    short: "test_intelligence_payloads.py",
    before: 1278,
    after: 100,
    parseMs: 5,
    compileMs: 4,
    pr: 357,
    status: "Ready",
    loop: 14,
  },
  {
    file: "tests/test_subscription_transports.py",
    short: "test_subscription_transports.py",
    before: 1235,
    after: 100,
    parseMs: 4,
    compileMs: 3,
    pr: 358,
    status: "Ready",
    loop: 15,
  },
  {
    file: "tests/test_task_rag.py",
    short: "test_task_rag.py",
    before: 1201,
    after: 100,
    parseMs: 5,
    compileMs: 4,
    pr: 359,
    status: "Ready",
    loop: 16,
  },
  {
    file: "tests/test_provider_harvest.py",
    short: "test_provider_harvest.py",
    before: 1188,
    after: 100,
    parseMs: 5.28,
    compileMs: 4.75,
    pr: 360,
    status: "Ready",
    loop: 17,
  },
  {
    file: "src/tools/swarm.py",
    short: "swarm.py",
    before: 1155,
    after: 120,
    parseMs: 5.21,
    compileMs: 4.12,
    pr: 361,
    status: "Ready",
    loop: 18,
  },
  {
    file: "src/mcp_session_guard.py",
    short: "mcp_session_guard.py",
    before: 1105,
    after: 120,
    parseMs: 4.25,
    compileMs: 3.02,
    pr: 362,
    status: "Ready",
    loop: 19,
  },
  {
    file: "tests/test_server.py",
    short: "test_server.py",
    before: 1110,
    after: 100,
    parseMs: 3.86,
    compileMs: 3.92,
    pr: 363,
    status: "Ready",
    loop: 20,
  },
  {
    file: "tests/test_provider_adapters.py",
    short: "test_provider_adapters.py",
    before: 1061,
    after: 100,
    parseMs: 4.49,
    compileMs: 2.88,
    pr: 364,
    status: "Ready",
    loop: 21,
  },
  {
    file: "tests/test_multiagent.py",
    short: "test_multiagent.py",
    before: 1102,
    after: 100,
    parseMs: 4.4,
    compileMs: 3.52,
    pr: 365,
    status: "Ready",
    loop: 22,
  },
  {
    file: "src/rag.py",
    short: "rag.py",
    before: 1001,
    after: 150,
    parseMs: 3.37,
    compileMs: 3.01,
    pr: 366,
    status: "Ready",
    loop: 23,
  },
  {
    file: "src/providers/mcp_sampling.py",
    short: "mcp_sampling.py",
    before: 986,
    after: 120,
    parseMs: 3.13,
    compileMs: 2.66,
    pr: 367,
    status: "Ready",
    loop: 24,
  },
  {
    file: "tests/test_knowledge.py",
    short: "test_knowledge.py",
    before: 1021,
    after: 100,
    parseMs: 4.53,
    compileMs: 3.34,
    pr: 368,
    status: "Ready",
    loop: 25,
  },
  {
    file: "tests/test_evals.py",
    short: "test_evals.py",
    before: 926,
    after: 100,
    parseMs: 3.66,
    compileMs: 3.19,
    pr: 369,
    status: "Ready",
    loop: 26,
  },
  {
    file: "src/providers/contracts.py",
    short: "contracts.py",
    before: 877,
    after: 100,
    parseMs: 3.35,
    compileMs: 2.42,
    pr: 370,
    status: "Ready",
    loop: 27,
  },
  {
    file: "src/provider_harvest.py",
    short: "provider_harvest.py",
    before: 816,
    after: 120,
    parseMs: 2.54,
    compileMs: 2.14,
    pr: 371,
    status: "Ready",
    loop: 28,
  },
  {
    file: "tests/test_credentials.py",
    short: "test_credentials.py",
    before: 804,
    after: 100,
    parseMs: 2.67,
    compileMs: 2.27,
    pr: 372,
    status: "Ready",
    loop: 29,
  },
  {
    file: "src/tools/chats.py",
    short: "chats.py",
    before: 800,
    after: 100,
    parseMs: 2.35,
    compileMs: 2,
    pr: 373,
    status: "Ready",
    loop: 30,
  },
  {
    file: "src/completion_envelope.py",
    short: "completion_envelope.py",
    before: 791,
    after: 100,
    parseMs: 2.98,
    compileMs: 2.04,
    pr: 374,
    status: "Ready",
    loop: 31,
  },
  {
    file: "tests/test_completion_envelope.py",
    short: "test_completion_envelope.py",
    before: 733,
    after: 90,
    parseMs: 2.48,
    compileMs: 1.83,
    pr: 375,
    status: "Ready",
    loop: 32,
  },
  {
    file: "tests/.../test_stage1_schema_safety.py",
    short: "test_stage1_schema_safety.py",
    before: 721,
    after: 90,
    parseMs: 1.99,
    compileMs: 1.95,
    pr: 376,
    status: "Ready",
    loop: 33,
  },
  {
    file: "tests/test_provider_attempt_ledger.py",
    short: "test_provider_attempt_ledger.py",
    before: 714,
    after: 90,
    parseMs: 2.48,
    compileMs: 1.86,
    pr: 377,
    status: "Ready",
    loop: 34,
  },
  {
    file: "tests/test_mcp_ui.py",
    short: "test_mcp_ui.py",
    before: 730,
    after: 90,
    parseMs: 2.93,
    compileMs: 2.07,
    pr: 378,
    status: "Ready",
    loop: 35,
  },
  {
    file: "tests/test_observability.py",
    short: "test_observability.py",
    before: 712,
    after: 90,
    parseMs: 2.51,
    compileMs: 2.13,
    pr: 379,
    status: "Ready",
    loop: 36,
  },
  {
    file: "tests/test_migrations.py",
    short: "test_migrations.py",
    before: 696,
    after: 90,
    parseMs: 2.38,
    compileMs: 1.59,
    pr: 380,
    status: "Ready",
    loop: 37,
  },
  {
    file: "src/workspace_memory.py",
    short: "workspace_memory.py",
    before: 668,
    after: 100,
    parseMs: 3.19,
    compileMs: 2.47,
    pr: 381,
    status: "Ready",
    loop: 38,
  },
  {
    file: "evals/runner.py",
    short: "runner.py",
    before: 659,
    after: 100,
    parseMs: 3.25,
    compileMs: 2.63,
    pr: 382,
    status: "Ready",
    loop: 39,
  },
  {
    file: "evals/.../provider_adapters.py",
    short: "provider_adapters.py",
    before: 623,
    after: 80,
    parseMs: 2.46,
    compileMs: 1.69,
    pr: 383,
    status: "Ready",
    loop: 40,
  },
  {
    file: "tests/.../test_attempt_ledger_safety.py",
    short: "test_attempt_ledger_safety.py",
    before: 612,
    after: 80,
    parseMs: 2.4,
    compileMs: 1.88,
    pr: 384,
    status: "Ready",
    loop: 41,
  },
  {
    file: "src/swarm/engine.py",
    short: "engine.py",
    before: 570,
    after: 80,
    parseMs: 2.33,
    compileMs: 1.62,
    pr: 385,
    status: "Ready",
    loop: 42,
  },
  {
    file: "tests/test_phase5.py",
    short: "test_phase5.py",
    before: 798,
    after: 90,
    parseMs: 2.98,
    compileMs: 2.19,
    pr: 386,
    status: "Ready",
    loop: 43,
  },
  {
    file: "tests/test_service_workspace_boundary.py",
    short: "test_service_workspace_boundary.py",
    before: 580,
    after: 80,
    parseMs: 2.28,
    compileMs: 1.73,
    pr: 387,
    status: "Ready",
    loop: 44,
  },
  {
    file: "evals/.../role_schemas.py",
    short: "role_schemas.py",
    before: 559,
    after: 70,
    parseMs: 2.08,
    compileMs: 1.48,
    pr: 388,
    status: "Ready",
    loop: 45,
  },
  {
    file: "evals/.../schemas.py",
    short: "schemas.py",
    before: 558,
    after: 70,
    parseMs: 1.65,
    compileMs: 1.41,
    pr: 389,
    status: "Ready",
    loop: 46,
  },
  {
    file: "tests/test_swarm_tools.py",
    short: "test_swarm_tools.py",
    before: 539,
    after: 70,
    parseMs: 2.5,
    compileMs: 1.65,
    pr: 390,
    status: "Ready",
    loop: 47,
  },
  {
    file: "src/intelligence_capsule.py",
    short: "intelligence_capsule.py",
    before: 539,
    after: 70,
    parseMs: 2.2,
    compileMs: 1.81,
    pr: 391,
    status: "Ready",
    loop: 48,
  },
  {
    file: "tests/.../test_provider_contract.py",
    short: "test_provider_contract.py",
    before: 533,
    after: 70,
    parseMs: 1.94,
    compileMs: 1.39,
    pr: 392,
    status: "Ready",
    loop: 49,
  },
  {
    file: "tests/test_intelligence_upgrade.py",
    short: "test_intelligence_upgrade.py",
    before: 549,
    after: 70,
    parseMs: 1.49,
    compileMs: 1.41,
    pr: 393,
    status: "Ready",
    loop: 50,
  },
  {
    file: "evals/.../provider_smoke.py",
    short: "provider_smoke.py",
    before: 491,
    after: 60,
    parseMs: 1.91,
    compileMs: 1.43,
    pr: 394,
    status: "Ready",
    loop: 51,
  },
  {
    file: "tests/test_swarm_engine.py",
    short: "test_swarm_engine.py",
    before: 481,
    after: 60,
    parseMs: 2.27,
    compileMs: 1.52,
    pr: 395,
    status: "Ready",
    loop: 52,
  },
  {
    file: "scripts/install_unigrok_theme.py",
    short: "install_unigrok_theme.py",
    before: 478,
    after: 60,
    parseMs: 1.81,
    compileMs: 1.36,
    pr: 396,
    status: "Ready",
    loop: 53,
  },
  {
    file: "tests/.../test_stage1_mock_harness.py",
    short: "test_stage1_mock_harness.py",
    before: 477,
    after: 60,
    parseMs: 2.22,
    compileMs: 1.5,
    pr: 397,
    status: "Ready",
    loop: 54,
  },
  {
    file: "scripts/land.py",
    short: "land.py",
    before: 489,
    after: 70,
    parseMs: 1.84,
    compileMs: 1.53,
    pr: 398,
    status: "Ready",
    loop: 55,
  },
  {
    file: "tests/test_release_hygiene.py",
    short: "test_release_hygiene.py",
    before: 468,
    after: 60,
    parseMs: 2.13,
    compileMs: 1.46,
    pr: 399,
    status: "Ready",
    loop: 56,
  },
  {
    file: "evals/.../validators.py",
    short: "validators.py",
    before: 465,
    after: 50,
    parseMs: 1.46,
    compileMs: 1.61,
    pr: 400,
    status: "Ready",
    loop: 57,
  },
  {
    file: "src/semantic_evals.py",
    short: "semantic_evals.py",
    before: 463,
    after: 60,
    parseMs: 1.18,
    compileMs: 1.14,
    pr: 401,
    status: "Ready",
    loop: 58,
  },
  {
    file: "tests/test_github_review_integration.py",
    short: "test_github_review_integration.py",
    before: 484,
    after: 60,
    parseMs: 1.57,
    compileMs: 1.25,
    pr: 402,
    status: "Ready",
    loop: 59,
  },
  {
    file: "tests/test_semantic_evals.py",
    short: "test_semantic_evals.py",
    before: 441,
    after: 55,
    parseMs: 1.75,
    compileMs: 1.31,
    pr: 403,
    status: "Ready",
    loop: 60,
  },
  {
    file: "scripts/bootstrap_intelligence_refs.py",
    short: "bootstrap_intelligence_refs.py",
    before: 435,
    after: 55,
    parseMs: 1.84,
    compileMs: 1.28,
    pr: 404,
    status: "Ready",
    loop: 61,
  },
  {
    file: "scripts/supervisor_approval.py",
    short: "supervisor_approval.py",
    before: 394,
    after: 50,
    parseMs: 1.65,
    compileMs: 1.75,
    pr: 405,
    status: "Ready",
    loop: 62,
  },
  {
    file: "src/metrics.py",
    short: "metrics.py",
    before: 422,
    after: 55,
    parseMs: 1.64,
    compileMs: 1.46,
    pr: 406,
    status: "Ready",
    loop: 63,
  },
  {
    file: "tests/.../test_provider_smoke.py",
    short: "test_provider_smoke.py",
    before: 407,
    after: 50,
    parseMs: 1.36,
    compileMs: 1.09,
    pr: 407,
    status: "Ready",
    loop: 64,
  },
  {
    file: "tests/test_install_unigrok_theme.py",
    short: "test_install_unigrok_theme.py",
    before: 395,
    after: 50,
    parseMs: 1.25,
    compileMs: 0.87,
    pr: 409,
    status: "Ready",
    loop: 65,
  },
  {
    file: "tests/test_intelligence_refs_bootstrap.py",
    short: "test_intelligence_refs_bootstrap.py",
    before: 389,
    after: 50,
    parseMs: 1.5,
    compileMs: 1.39,
    pr: 410,
    status: "Ready",
    loop: 66,
  },
  {
    file: "tests/test_metrics.py",
    short: "test_metrics.py",
    before: 388,
    after: 50,
    parseMs: 1.93,
    compileMs: 1.26,
    pr: 411,
    status: "Ready",
    loop: 67,
  },
  {
    file: "src/providers/base.py",
    short: "base.py",
    before: 380,
    after: 45,
    parseMs: 1.11,
    compileMs: 1.05,
    pr: 412,
    status: "Ready",
    loop: 68,
  },
  {
    file: "scripts/check_agent_attribution.py",
    short: "check_agent_attribution.py",
    before: 369,
    after: 45,
    parseMs: 1.69,
    compileMs: 1.54,
    pr: 413,
    status: "Ready",
    loop: 69,
  },
  {
    file: "tests/test_workspace_memory.py",
    short: "test_workspace_memory.py",
    before: 365,
    after: 45,
    parseMs: 1.75,
    compileMs: 1.14,
    pr: 414,
    status: "Ready",
    loop: 70,
  },
  {
    file: "scripts/github-grok-review.py",
    short: "github-grok-review.py",
    before: 350,
    after: 45,
    parseMs: 1.61,
    compileMs: 1.46,
    pr: 415,
    status: "Ready",
    loop: 71,
  },
  {
    file: "src/jobs.py",
    short: "jobs.py",
    before: 381,
    after: 45,
    parseMs: 1.23,
    compileMs: 1.5,
    pr: 416,
    status: "Ready",
    loop: 72,
  },
  {
    file: "tests/test_land_workflow.py",
    short: "test_land_workflow.py",
    before: 388,
    after: 45,
    parseMs: 2.01,
    compileMs: 1.7,
    pr: 417,
    status: "Ready",
    loop: 73,
  },
  {
    file: "tests/test_xai_client_authority.py",
    short: "test_xai_client_authority.py",
    before: 334,
    after: 45,
    parseMs: 1.13,
    compileMs: 1.02,
    pr: 418,
    status: "Ready",
    loop: 74,
  },
  {
    file: "src/storage.py",
    short: "storage.py",
    before: 342,
    after: 40,
    parseMs: 1.27,
    compileMs: 0.98,
    pr: 419,
    status: "Ready",
    loop: 75,
  },
  {
    file: "tests/test_swarm_storage.py",
    short: "test_swarm_storage.py",
    before: 312,
    after: 40,
    parseMs: 1.32,
    compileMs: 1.1,
    pr: 420,
    status: "Ready",
    loop: 76,
  },
];

const TOTAL_INVENTORY = 210;
const SNAPSHOT = "Tracker 2026-07-16T17:12:45Z · projected facade/shim LOC";

function pctChange(before: number, after: number): number {
  if (before <= 0) return 0;
  return ((after - before) / before) * 100;
}

function fmtPct(p: number): string {
  const sign = p > 0 ? "+" : "";
  return `${sign}${p.toFixed(1)}%`;
}

function fmtLoc(n: number): string {
  return n.toLocaleString("en-US");
}

function pctTone(
  p: number,
): "success" | "warning" | "danger" | "neutral" | "info" {
  if (p <= -40) return "success";
  if (p <= -10) return "info";
  if (p < 5 && p > -5) return "neutral";
  if (p >= 5) return "danger";
  return "warning";
}

function pctColor(
  p: number,
  theme: ReturnType<typeof useHostTheme>,
): string {
  if (p <= -40) return theme.category.green;
  if (p <= -10) return theme.category.cyan;
  if (p < 5 && p > -5) return theme.text.tertiary;
  if (p >= 5) return theme.category.red;
  return theme.category.yellow;
}

type SortMode = "biggest_win" | "loc_before" | "pr";
type ViewMode = "ready" | "all";

export default function PythonSuperiorityLoopMetrics() {
  const theme = useHostTheme();
  const [sort, setSort] = useCanvasState<SortMode>("sort", "biggest_win");
  const [view, setView] = useCanvasState<ViewMode>("view", "ready");
  const [showChart, setShowChart] = useCanvasState("showChart", true);

  const withPct = FILES.map((f) => ({
    ...f,
    pct: pctChange(f.before, f.after),
    delta: f.after - f.before,
    latMs: f.parseMs + f.compileMs,
  }));

  const readyOnly = view === "ready";
  const filtered = readyOnly
    ? withPct.filter((f) => f.status === "Ready")
    : withPct;

  const sorted = [...filtered].sort((a, b) => {
    if (sort === "biggest_win") return a.pct - b.pct;
    if (sort === "loc_before") return b.before - a.before;
    return a.pr - b.pr;
  });

  const totalBefore = withPct.reduce((s, f) => s + f.before, 0);
  const totalAfter = withPct.reduce((s, f) => s + f.after, 0);
  const overallPct = pctChange(totalBefore, totalAfter);
  const avgPct =
    withPct.reduce((s, f) => s + f.pct, 0) / Math.max(withPct.length, 1);
  const readyCount = withPct.filter((f) => f.status === "Ready").length;
  const doneCount = FILES.length;
  const pendingCount = TOTAL_INVENTORY - doneCount;
  const progressPct = (doneCount / TOTAL_INVENTORY) * 100;

  const chartTop = [...withPct]
    .sort((a, b) => b.before - a.before)
    .slice(0, 8);

  const topWins = [...withPct].sort((a, b) => a.pct - b.pct).slice(0, 5);

  return (
    <Stack
      gap={24}
      style={{
        padding: 28,
        background: theme.bg.editor,
        minHeight: "100%",
      }}
    >
      <Stack gap={8}>
        <Row gap={10} align="center">
          <Text size="small" tone="secondary" weight="semibold">
            Cursor × UniGrok
          </Text>
          <Pill size="sm" active>
            Python Superiority Loop
          </Pill>
          <Spacer />
          <Text size="small" tone="tertiary">
            {SNAPSHOT}
          </Text>
        </Row>
        <H1>Projected LOC cut — live % board</H1>
        <Text tone="secondary">
          Before → after facade/shim projections from the superiority-loop
          tracker. Green = reduction. Codex Sol High can watch Ready draft
          PRs; merge stays the normal Codex land path.
        </Text>
      </Stack>

      <Grid columns={4} gap={16}>
        <Stat
          value={fmtPct(overallPct)}
          label="Overall LOC reduction"
          tone="success"
        />
        <Stat
          value={fmtPct(avgPct)}
          label="Avg file % cut"
          tone="success"
        />
        <Stat
          value={`${doneCount} / ${TOTAL_INVENTORY}`}
          label="Files with plans"
          tone="info"
        />
        <Stat
          value={String(readyCount)}
          label="Ready draft PRs"
          tone="info"
        />
      </Grid>

      <Card>
        <CardHeader trailing={`${progressPct.toFixed(0)}% of inventory`}>
          Loop progress
        </CardHeader>
        <CardBody>
          <Stack gap={12}>
            <UsageBar
              total={TOTAL_INVENTORY}
              topLeftLabel={`${doneCount} plans Ready`}
              topRightLabel={`${pendingCount} pending · ${TOTAL_INVENTORY} total`}
              segments={[
                { id: "ready", value: doneCount, color: "green" },
                { id: "pending", value: pendingCount, color: "gray" },
              ]}
            />
            <Row gap={16} wrap>
              <Text size="small" tone="secondary">
                LOC {fmtLoc(totalBefore)} → {fmtLoc(totalAfter)} (
                {fmtLoc(totalAfter - totalBefore)} lines)
              </Text>
              <Text size="small" tone="tertiary">
                Next pending: tests/test_supervisor_approval.py (~301 LOC)</Text>
            </Row>
          </Stack>
        </CardBody>
      </Card>

      <Stack gap={8}>
        <H2>Biggest % wins</H2>
        <Grid columns={5} gap={12}>
          {topWins.map((f) => (
            <div key={f.pr}>
              <Card size="lg">
                <CardHeader trailing={`#${f.pr}`}>{f.short}</CardHeader>
                <CardBody>
                  <Stack gap={4}>
                    <Text
                      weight="bold"
                      style={{
                        fontSize: 22,
                        color: pctColor(f.pct, theme),
                        lineHeight: 1.2,
                      }}
                    >
                      {fmtPct(f.pct)}
                    </Text>
                    <Text size="small" tone="secondary">
                      {fmtLoc(f.before)} → {fmtLoc(f.after)} LOC
                    </Text>
                  </Stack>
                </CardBody>
              </Card>
            </div>
          ))}
        </Grid>
      </Stack>

      <Stack gap={10}>
        <Row gap={12} align="center" wrap>
          <H2>File metrics</H2>
          <Spacer />
          <Row gap={6} align="center">
            <Text size="small" tone="secondary">
              Ready only
            </Text>
            <Toggle
              checked={readyOnly}
              onChange={(on) => setView(on ? "ready" : "all")}
            />
          </Row>
          <Row gap={6} align="center">
            <Text size="small" tone="secondary">
              LOC chart
            </Text>
            <Toggle checked={showChart} onChange={setShowChart} />
          </Row>
          <Select
            value={sort}
            onChange={(v) => setSort(v as SortMode)}
            options={[
              { value: "biggest_win", label: "Sort: biggest % win" },
              { value: "loc_before", label: "Sort: LOC before" },
              { value: "pr", label: "Sort: PR #" },
            ]}
          />
        </Row>

        {showChart ? (
          <Stack gap={6}>
            <Text size="small" tone="secondary" weight="semibold">
              Top 8 files — projected LOC before vs after (facade/shim)
            </Text>
            <BarChart
              categories={chartTop.map((f) => f.short)}
              series={[
                {
                  name: "Before LOC",
                  data: chartTop.map((f) => f.before),
                  tone: "warning",
                },
                {
                  name: "After LOC (projected)",
                  data: chartTop.map((f) => f.after),
                  tone: "success",
                },
              ]}
              height={220}
              valueSuffix=" LOC"
              horizontal
              showValues={false}
            />
            <Text size="small" tone="tertiary">
              Source: python-superiority-loop tracker · projected after =
              facade/shim target (modules split out)
            </Text>
          </Stack>
        ) : null}

        <Table
          headers={[
            "File",
            "Before",
            "After",
            "Δ LOC",
            "Δ%",
            "Latency (baseline)",
            "PR",
            "Status",
          ]}
          columnAlign={[
            "left",
            "right",
            "right",
            "right",
            "right",
            "left",
            "left",
            "left",
          ]}
          striped
          stickyHeader
          rowTone={sorted.map((f) => pctTone(f.pct))}
          rows={sorted.map((f) => [
            <Text size="small" as="span" truncate>
              {f.file}
            </Text>,
            fmtLoc(f.before),
            fmtLoc(f.after),
            fmtLoc(f.delta),
            <Text
              as="span"
              weight="semibold"
              size="small"
              style={{ color: pctColor(f.pct, theme) }}
            >
              {fmtPct(f.pct)}
            </Text>,
            `parse ${f.parseMs}ms / compile ${f.compileMs}ms`,
            <Link
              href={`https://github.com/djtelicloud/grok-mcp-server/pull/${f.pr}`}
            >
              #{f.pr}
            </Link>,
            f.status === "Ready" ? (
              <Pill size="sm" active>
                Ready
              </Pill>
            ) : (
              <Pill size="sm">draft</Pill>
            ),
          ])}
        />
        <Text size="small" tone="tertiary">
          Δ% = (after − before) / before × 100. Latency is current-file
          baseline only — after-parse not recorded in tracker. Memory: n/a
          across this batch.
        </Text>
      </Stack>

      <Divider />

      <Callout tone="info" title="Watcher handoff">
        <Text size="small">
          {readyCount} Ready refactor_plan drafts (#
          {FILES[0].pr}–#{FILES[FILES.length - 1].pr}) are queued for Codex
          Sol High review. Land/merge remains the normal Codex supervisor
          path — this board is visibility only.
        </Text>
      </Callout>
    </Stack>
  );
}
