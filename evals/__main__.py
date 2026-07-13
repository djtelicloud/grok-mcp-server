# evals/__main__.py
# CLI for the self-feeding eval harness.
#
#   python -m evals run [--live] [--check-baseline] [--require-pass]
#                       [--task ID ...]
#                       [--tasks-dir D] [--cassettes-dir D] [--out D]
#                       [--baseline F] [--no-calibration] [--calibration-db F]
#   python -m evals export-session NAME [--db F] [--task-id ID] [--category C]
#
# Offline runs are hermetic: UNI_GROK_TESTING=1 plus a throwaway chats dir so
# replay never touches the production session db — EXCEPT the calibration
# write, which intentionally targets the production store (that closed loop
# is the point). --live skips the hermetic setup and calls the real API;
# never wire --live into CI.

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path


def _production_chats_db() -> Path:
    """The production session db path, computed WITHOUT the testing-mode
    redirect (this must be resolved before the hermetic env is applied)."""
    from src.utils import PathResolver

    state_base = PathResolver.get_state_base_dir()
    chats_dir = (state_base / "chats") if state_base else (PathResolver.get_project_root() / "chats")
    return chats_dir / "grok_sessions.db"


def _apply_hermetic_env():
    os.environ["UNI_GROK_TESTING"] = "1"
    if not os.environ.get("UNI_GROK_TEST_CHATS_DIR"):
        os.environ["UNI_GROK_TEST_CHATS_DIR"] = tempfile.mkdtemp(prefix="unigrok-evals-")


async def _cmd_run(args) -> int:
    from evals import runner
    from evals.cassettes import load_cassettes
    from src.utils import GrokSessionStore, store as global_store

    tasks = runner.load_tasks(args.tasks_dir, only_ids=args.task or None)
    if not tasks:
        print("no golden tasks found", file=sys.stderr)
        return 2

    try:
        notes = []
        if args.live:
            results, batch_note = await runner.run_live(tasks)
            notes.append(batch_note)
            run_mode = "live"
        else:
            cassettes = load_cassettes(args.cassettes_dir)
            results = await runner.run_offline(tasks, cassettes)
            run_mode = "offline"

        # Closed loop: aggregate outcomes into routing_calibration so the
        # RoutingAdvisor consumes them for borderline decisions.
        if not args.no_calibration:
            calibration_store = GrokSessionStore(db_path=Path(args.calibration_db))
            try:
                written = await runner.write_calibration(calibration_store, results)
                notes.append(f"calibration: {written} row(s) upserted into {args.calibration_db}")
            finally:
                await calibration_store.close()

        report = runner.build_report(results, run_mode=run_mode, notes=notes)
        report_path = runner.write_report(report, args.out)
        print(runner.markdown_summary(report))
        print(f"\nreport: {report_path}")

        if args.check_baseline:
            regressions = runner.check_baseline(results, args.baseline)
            if regressions:
                print(
                    f"\nBASELINE REGRESSION: {len(regressions)} expected-pass task(s) failed: "
                    f"{', '.join(regressions)}",
                    file=sys.stderr,
                )
                return 1
            print("\nbaseline check: OK (no regressions)")
        if args.require_pass and report["totals"]["failed"]:
            print(
                f"\nRUN FAILED: {report['totals']['failed']} task(s) did not pass",
                file=sys.stderr,
            )
            return 1
        return 0
    finally:
        # Session seeding/persistence ran through the global store —
        # aiosqlite connections own non-daemon worker threads, so an unclosed
        # store hangs interpreter shutdown.
        await global_store.close()


async def _cmd_export_session(args) -> int:
    from evals.cassettes import DEFAULT_CASSETTES_DIR, export_session
    from evals.runner import DEFAULT_TASKS_DIR
    from src.utils import GrokSessionStore

    store = GrokSessionStore(db_path=Path(args.db))
    try:
        exported = await export_session(
            store,
            args.name,
            tasks_dir=Path(args.tasks_dir or DEFAULT_TASKS_DIR),
            cassettes_dir=Path(args.cassettes_dir or DEFAULT_CASSETTES_DIR),
            category=args.category,
            task_id=args.task_id,
        )
    except ValueError as exc:
        print(f"export-session failed: {exc}", file=sys.stderr)
        return 2
    finally:
        await store.close()
    print(json.dumps(exported, indent=2))
    print(
        f"\nSession '{args.name}' is now golden task '{exported['task_id']}' — "
        "review the generated graders, then add the id to evals/baseline.json."
    )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run golden tasks (offline cassette replay by default)")
    run_p.add_argument("--live", action="store_true",
                       help="run against the real xAI API (never in CI)")
    run_p.add_argument("--check-baseline", action="store_true",
                       help="exit 1 on any regression vs the expected-pass baseline")
    run_p.add_argument("--require-pass", action="store_true",
                       help="exit 1 when any selected task fails")
    run_p.add_argument("--task", action="append", metavar="ID",
                       help="run only these task id(s); repeatable")
    run_p.add_argument("--tasks-dir", type=Path, default=None)
    run_p.add_argument("--cassettes-dir", type=Path, default=None)
    run_p.add_argument("--out", type=Path, default=None, help="report output dir (default evals/out)")
    run_p.add_argument("--baseline", type=Path, default=None, help="baseline file (default evals/baseline.json)")
    run_p.add_argument("--no-calibration", action="store_true",
                       help="skip the routing_calibration upsert")
    run_p.add_argument("--calibration-db", type=Path, default=None,
                       help="target db for calibration rows (default: production session db)")

    exp_p = sub.add_parser("export-session",
                           help="turn a real stored session into a golden task + cassette")
    exp_p.add_argument("name", help="session name in the session store")
    exp_p.add_argument("--db", type=Path, default=None, help="session db (default: production session db)")
    exp_p.add_argument("--task-id", default=None)
    exp_p.add_argument("--category", default="memory",
                       choices=["coding", "reasoning", "research", "memory"])
    exp_p.add_argument("--tasks-dir", type=Path, default=None)
    exp_p.add_argument("--cassettes-dir", type=Path, default=None)

    args = parser.parse_args(argv)

    # A dummy key keeps src.utils importable for offline work; live runs need
    # the real credential already exported.
    os.environ.setdefault("XAI_API_KEY", "eval-offline-dummy-key")

    # The server's INFO logging would drown the markdown summary; keep the
    # CLI quiet unless explicitly asked for.
    if os.environ.get("UNIGROK_EVAL_VERBOSE", "").strip().lower() not in ("1", "true", "yes"):
        logging.getLogger("GrokMCP").setLevel(logging.ERROR)

    # Resolve production db paths BEFORE any hermetic redirect.
    production_db = _production_chats_db()

    if args.command == "run":
        if args.calibration_db is None:
            args.calibration_db = production_db
        if not args.live:
            _apply_hermetic_env()
        return asyncio.run(_cmd_run(args))

    if args.command == "export-session":
        if args.db is None:
            args.db = production_db
        return asyncio.run(_cmd_export_session(args))

    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
