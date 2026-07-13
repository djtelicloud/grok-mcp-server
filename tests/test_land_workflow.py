import importlib.util
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_land_module():
    spec = importlib.util.spec_from_file_location("unigrok_land", ROOT / "scripts" / "land.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


land = load_land_module()


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo_with_agent(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "main"
    agent = tmp_path / "agent"
    main.mkdir()
    git(main, "init", "-b", "main")
    git(main, "config", "user.name", "Test Agent")
    git(main, "config", "user.email", "agent@example.test")
    write(main / "file.txt", "base\n")
    git(main, "add", "file.txt")
    git(main, "commit", "-m", "base")
    git(main, "worktree", "add", "-b", "codex/task", str(agent), "main")
    return main, agent


def commit(agent: Path, text: str) -> str:
    write(agent / "file.txt", text)
    git(agent, "add", "file.txt")
    git(agent, "commit", "-m", text.strip())
    return git(agent, "rev-parse", "HEAD")


def configure_fast_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(land, "run_tests", lambda repo, expected_head: None)
    monkeypatch.setattr(land, "reconcile_runtime", lambda repo, paths: "test skipped")


def test_run_tests_checks_attribution_and_generated_okf_before_pytest(
    monkeypatch, tmp_path
):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(land, "run", fake_run)
    monkeypatch.setattr(land, "git", lambda repo, *args, **kwargs: "abc123")
    monkeypatch.setattr(land, "require_clean", lambda *args, **kwargs: None)

    land.run_tests(tmp_path, "abc123")

    assert calls[0] == [
        "uv",
        "run",
        "python",
        "scripts/check_agent_attribution.py",
        "--base-ref",
        land.MAIN_REF,
        "--head",
        "abc123",
    ]
    assert calls[1] == ["uv", "run", "python", "scripts/generate_okf.py", "--check"]
    assert calls[2] == land.DEFAULT_TEST_ARGS


def test_runtime_action_is_minimal():
    assert land.runtime_action(["README.md", "tests/test_x.py"]) == "none"
    assert land.runtime_action(["mcp_ui/index.html"]) == "smoke"
    assert land.runtime_action(["docs/okf/api-reference.md"]) == "smoke"
    assert land.runtime_action(["src/server.py"]) == "restart"
    assert land.runtime_action(["uv.lock"]) == "rebuild"
    assert land.runtime_action(["docker-compose.dev.yml"]) == "rebuild"


def test_land_fast_forwards_visible_main(repo_with_agent, monkeypatch):
    main, agent = repo_with_agent
    expected = commit(agent, "agent change\n")
    configure_fast_test(monkeypatch)

    landed = land.land(agent)

    assert landed == expected
    assert git(main, "rev-parse", "HEAD") == expected
    assert (main / "file.txt").read_text(encoding="utf-8") == "agent change\n"
    receipt = main / ".git" / "unigrok-land" / "receipts" / f"{expected}.json"
    assert receipt.is_file()
    receipt_data = __import__("json").loads(receipt.read_text(encoding="utf-8"))
    assert receipt_data["head"] == expected
    assert receipt_data["previous_main"]
    assert receipt_data["changed_paths"] == ["file.txt"]
    assert receipt_data["tests"]["status"] == "passed"
    marker = main / ".git" / "unigrok-land" / "runtime-head"
    assert marker.read_text(encoding="utf-8").strip() == expected

    original_receipt = receipt.read_bytes()
    assert land.land(agent) == expected
    assert receipt.read_bytes() == original_receipt


def test_land_rebases_behind_agent_before_testing(repo_with_agent, monkeypatch):
    main, agent = repo_with_agent
    write(main / "main-only.txt", "other agent\n")
    git(main, "add", "main-only.txt")
    git(main, "commit", "-m", "other agent")
    commit(agent, "agent change\n")
    configure_fast_test(monkeypatch)

    landed = land.land(agent)

    assert git(main, "rev-parse", "HEAD") == landed
    assert (main / "main-only.txt").read_text(encoding="utf-8") == "other agent\n"
    assert (main / "file.txt").read_text(encoding="utf-8") == "agent change\n"


def test_land_retests_when_main_advances_during_tests(repo_with_agent, monkeypatch, tmp_path):
    main, agent = repo_with_agent
    commit(agent, "agent change\n")
    marker = tmp_path / "advanced"
    calls = 0

    def advance_once(repo, expected_head):
        nonlocal calls
        calls += 1
        if marker.exists():
            return
        write(main / "raced.txt", "landed while tests ran\n")
        git(main, "add", "raced.txt")
        git(main, "commit", "-m", "concurrent landing")
        marker.write_text("done", encoding="utf-8")

    monkeypatch.setattr(land, "run_tests", advance_once)
    monkeypatch.setattr(land, "reconcile_runtime", lambda repo, paths: "test skipped")

    landed = land.land(agent)

    assert git(main, "rev-parse", "HEAD") == landed
    assert (main / "raced.txt").read_text(encoding="utf-8") == "landed while tests ran\n"
    assert (main / "file.txt").read_text(encoding="utf-8") == "agent change\n"
    assert calls == 2


def test_land_refuses_dirty_visible_main(repo_with_agent, monkeypatch):
    main, agent = repo_with_agent
    commit(agent, "agent change\n")
    write(main / "file.txt", "unsaved IDE edit\n")
    configure_fast_test(monkeypatch)

    with pytest.raises(land.LandError, match="shared main worktree is dirty"):
        land.land(agent)

    assert (main / "file.txt").read_text(encoding="utf-8") == "unsaved IDE edit\n"


def test_failed_runtime_reconciliation_is_retried_from_previous_main(repo_with_agent, monkeypatch):
    main, agent = repo_with_agent
    expected = commit(agent, "agent change\n")
    monkeypatch.setattr(land, "run_tests", lambda repo, expected_head: None)

    def fail_runtime(repo, paths):
        assert paths == ["file.txt"]
        raise land.LandError("runtime unavailable")

    monkeypatch.setattr(land, "reconcile_runtime", fail_runtime)
    with pytest.raises(land.LandError, match="runtime unavailable"):
        land.land(agent)

    assert git(main, "rev-parse", "HEAD") == expected
    failed_receipt = main / ".git" / "unigrok-land" / "receipts" / f"{expected}.json"
    assert not failed_receipt.exists()
    seen = []
    monkeypatch.setattr(land, "reconcile_runtime", lambda repo, paths: seen.extend(paths) or "recovered")

    assert land.land(agent) == expected
    assert seen == ["file.txt"]
    marker = main / ".git" / "unigrok-land" / "runtime-head"
    assert marker.read_text(encoding="utf-8").strip() == expected
    receipt = main / ".git" / "unigrok-land" / "receipts" / f"{expected}.json"
    receipt_data = __import__("json").loads(receipt.read_text(encoding="utf-8"))
    assert receipt_data["changed_paths"] == ["file.txt"]


def test_land_refuses_uncommitted_agent_work(repo_with_agent, monkeypatch):
    _, agent = repo_with_agent
    write(agent / "uncommitted.txt", "not committed\n")
    configure_fast_test(monkeypatch)

    with pytest.raises(land.LandError, match="agent worktree is dirty"):
        land.land(agent)


def test_land_must_not_run_from_main(repo_with_agent, monkeypatch):
    main, _ = repo_with_agent
    configure_fast_test(monkeypatch)

    with pytest.raises(land.LandError, match="never from shared main"):
        land.land(main)


def test_land_refuses_non_codex_contributor_branch(repo_with_agent, monkeypatch):
    _, agent = repo_with_agent
    git(agent, "branch", "-m", "gemini/task")
    commit(agent, "agent change\n")
    configure_fast_test(monkeypatch)

    with pytest.raises(land.LandError, match="only a Codex-owned integration branch"):
        land.land(agent)


def test_dead_process_lock_is_recovered(tmp_path: Path):
    lock = tmp_path / "land.lock"
    lock.mkdir()
    write(
        lock / "owner.json",
        '{"pid": 99999999, "host": "' + land.socket.gethostname() + '", "started": 1}',
    )

    with land.directory_lock(lock, timeout=0.2):
        assert lock.is_dir()

    assert not lock.exists()


def test_runtime_wait_retries_expected_restart_handoff(monkeypatch, tmp_path):
    attempts = 0

    def flaky_probe(url, status):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionResetError("container restarting")

    monkeypatch.setattr(land, "probe_json", flaky_probe)
    monkeypatch.setattr(land, "probe_ui", lambda base_url: None)
    monkeypatch.setattr(land, "probe_okf", lambda base_url: None)
    monkeypatch.setattr(
        land,
        "get_json",
        lambda url: {"gateway_auth": {"enabled": False}},
    )
    monkeypatch.setattr(land, "probe_mcp", lambda base_url, token=None: None)
    monkeypatch.setattr(land.time, "sleep", lambda seconds: None)

    land.wait_for_runtime(tmp_path, base_url="http://127.0.0.1:4766", timeout=1.0)

    assert attempts >= 3


def test_runtime_reconciliation_only_inspects_contributor_compose(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(land, "run", fake_run)

    result = land.reconcile_runtime(tmp_path, ["src/server.py"])

    assert result == "contributor dev service not running; stable service untouched"
    assert calls == [[
        "docker", "compose", "-p", "grok-mcp-dev", "-f", "docker-compose.dev.yml",
        "ps", "--status", "running", "--services",
    ]]
