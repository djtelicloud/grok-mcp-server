#!/usr/bin/env python3
"""Compare the checkout's public runtime source with a running Docker container."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from unigrok_public.build_identity import manifest_fingerprint, source_manifest

_CONTAINER_MANIFEST_PROGRAM = r"""
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
if not root.is_dir():
    print(f"source root is not a directory: {root}", file=sys.stderr)
    raise SystemExit(2)

manifest = {}
for path in sorted(root.rglob("*")):
    relative = path.relative_to(root)
    if (
        not path.is_file()
        or "__pycache__" in relative.parts
        or path.name == ".DS_Store"
        or path.suffix in {".pyc", ".pyo"}
    ):
        continue
    manifest[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps(manifest, sort_keys=True))
"""


def compare_manifests(host: dict[str, str], runtime: dict[str, str]) -> dict[str, Any]:
    """Return a stable, machine-readable source comparison."""
    host_paths = set(host)
    runtime_paths = set(runtime)
    return {
        "status": "match" if host == runtime else "drift",
        "host_fingerprint": manifest_fingerprint(host),
        "runtime_fingerprint": manifest_fingerprint(runtime),
        "changed": sorted(
            path for path in host_paths & runtime_paths if host[path] != runtime[path]
        ),
        "missing_in_runtime": sorted(host_paths - runtime_paths),
        "extra_in_runtime": sorted(runtime_paths - host_paths),
    }


def _runtime_manifest(container: str, root: str) -> dict[str, str]:
    docker = shutil.which("docker")
    if docker is None:
        raise RuntimeError("docker executable was not found")
    completed = subprocess.run(
        [docker, "exec", container, "python", "-c", _CONTAINER_MANIFEST_PROGRAM, root],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "docker exec failed"
        raise RuntimeError(detail[:500])
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict) or not all(
        isinstance(path, str) and isinstance(digest, str)
        for path, digest in payload.items()
    ):
        raise RuntimeError("container returned an invalid source manifest")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prove whether the current checkout and a running UniGrok container "
            "contain byte-identical public runtime sources."
        )
    )
    parser.add_argument("--container", default="unigrok")
    parser.add_argument(
        "--host-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "src" / "unigrok_public",
    )
    parser.add_argument("--container-root", default="/app/src/unigrok_public")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = compare_manifests(
            source_manifest(args.host_root),
            _runtime_manifest(args.container, args.container_root),
        )
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"runtime-parity: ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"runtime-parity: {report['status'].upper()}")
        print(f"  checkout: {report['host_fingerprint']}")
        print(f"  container: {report['runtime_fingerprint']}")
        for label in ("changed", "missing_in_runtime", "extra_in_runtime"):
            paths = report[label]
            if paths:
                print(f"  {label.replace('_', ' ')} ({len(paths)}):")
                for path in paths:
                    print(f"    {path}")
    return 0 if report["status"] == "match" else 1


if __name__ == "__main__":
    raise SystemExit(main())
