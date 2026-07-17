from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

EXPECTED_TOOLS = [
    "agent",
    "agent_result",
    "review_pull_request",
    "chat",
    "grok_mcp_discover_self",
    "grok_mcp_onboard_client",
    "grok_mcp_status",
    "benchmark_status",
    "record_benchmark_result",
    "list_models",
    "list_sessions",
    "session_history",
    "forget_session",
    "remember_fact",
    "search_knowledge",
    "forget_fact",
    "web_search",
    "x_search",
    "remote_code_execution",
    "chat_with_vision",
    "chat_with_files",
    "generate_image",
    "generate_video",
    "extend_video",
    "xai_upload_file",
    "xai_list_files",
    "xai_get_file",
    "xai_get_file_content",
    "xai_delete_file",
]
EXPECTED_VERSION = "1.1.0"


def _structured(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        value = structured.get("result", structured)
        return value if isinstance(value, dict) else {}
    for item in getattr(result, "content", []):
        raw = getattr(item, "text", None)
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    return {}


async def _agent_call(session: ClientSession, arguments: dict[str, Any]) -> tuple[Any, dict]:
    name = "agent"
    for _ in range(20):
        result = await session.call_tool(name, arguments)
        payload = _structured(result)
        if result.isError or payload.get("status") != "pending":
            return result, payload
        name = "agent_result"
        arguments = {"job_id": payload["job_id"], "wait_seconds": 16}
    raise RuntimeError("agent job remained pending for too long")


async def smoke(
    url: str, invoke_cli: bool, invoke_api: bool, invoke_research: bool, invoke_code: bool
) -> None:
    async with streamablehttp_client(url, headers={"X-Client-ID": "release-smoke"}) as (
        read,
        write,
        _,
    ):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            if initialized.serverInfo.version != EXPECTED_VERSION:
                raise RuntimeError(
                    "MCP handshake version mismatch: "
                    f"{initialized.serverInfo.version} != {EXPECTED_VERSION}"
                )
            instructions = initialized.instructions or ""
            if "makes web research, X search, and code execution available" not in instructions:
                raise RuntimeError("MCP handshake omitted the all-tools default contract")
            if "Inform the user" not in instructions:
                raise RuntimeError("MCP handshake omitted the tool disclosure requirement")
            if ".agents/skills/<skill-name>/SKILL.md" not in instructions:
                raise RuntimeError("MCP handshake omitted project onboarding paths")
            print(f"version={initialized.serverInfo.version}")
            listed = await session.list_tools()
            names = [tool.name for tool in listed.tools]
            if names != EXPECTED_TOOLS:
                raise RuntimeError(f"unexpected tools: {names}")
            print(f"tools={len(names)}")

            status_result = await session.call_tool("grok_mcp_status", {"refresh": True})
            if status_result.isError:
                raise RuntimeError("grok_mcp_status failed")
            status = _structured(status_result)
            if status.get("tool_count") != len(EXPECTED_TOOLS):
                raise RuntimeError("status tool_count does not match tools/list")
            print(
                "planes="
                f"cli:{str(status.get('cli', {}).get('ready', False)).lower()},"
                f"api:{str(status.get('api', {}).get('ready', False)).lower()}"
            )

            discover_result = await session.call_tool("grok_mcp_discover_self", {})
            discover = _structured(discover_result)
            if [tool.get("name") for tool in discover.get("tools", [])] != EXPECTED_TOOLS:
                raise RuntimeError("self-description does not match tools/list")
            planes = discover.get("credential_planes", {})
            if not {"cli", "api", "notices"}.issubset(planes):
                raise RuntimeError("self-description omitted credential planes")
            bootstrap = discover.get("bootstrap", {})
            if not bootstrap.get("can_chat"):
                raise RuntimeError("self-description says chat is unavailable")
            defaults = discover.get("capability_defaults", {})
            if defaults.get("agent", {}).get("allow_web") is not True:
                raise RuntimeError("agent web research is not enabled by default")
            if defaults.get("agent", {}).get("allow_x_search") is not True:
                raise RuntimeError("agent X search is not enabled by default")
            if defaults.get("agent", {}).get("allow_remote_code_execution") is not True:
                raise RuntimeError("agent remote code is not enabled by default")
            if defaults.get("agent", {}).get("user_notice_required") is not True:
                raise RuntimeError("agent tool disclosure requirement is missing")
            onboarding = discover.get("project_onboarding", {})
            if onboarding.get("canonical_paths", {}).get("antigravity_rules") != (
                ".agents/rules/<rule-name>.md"
            ):
                raise RuntimeError("project onboarding paths are missing or stale")
            client_onboarding = discover.get("client_onboarding", {})
            if client_onboarding.get("recommended_scope") != "global":
                raise RuntimeError("global client onboarding is missing or stale")
            if client_onboarding.get("automatic_writes") is not False:
                raise RuntimeError("client onboarding misstated the no-write boundary")
            if status.get("version") != discover.get("version"):
                raise RuntimeError("status and discovery versions disagree")
            if not {"lead", "specialists", "caller_controls"}.issubset(
                discover.get("routing", {})
            ):
                raise RuntimeError("self-description omitted routing contract")
            team = discover.get("team_harness", {})
            if not team.get("named_sessions") or team.get("state_backend") != "local_sqlite":
                raise RuntimeError("self-description omitted durable team state")
            if team.get("local_subagents") is not False:
                raise RuntimeError("self-description misstated the local subagent boundary")
            print("self_description=ok")

            destructive_probe = await session.call_tool(
                "forget_session", {"session": "smoke:confirmation-probe"}
            )
            if not destructive_probe.isError:
                raise RuntimeError("destructive tool accepted an unconfirmed request")
            print("destructive_gate=ok")

            if invoke_cli:
                result, payload = await _agent_call(
                    session,
                    {
                        "task": "Reply exactly with: SEQUENTIAL_TEST_GROK",
                    },
                )
                enabled = payload.get("agent_tools", {}).get("enabled", {})
                tools_receipt = payload.get("agent_tools", {})
                if result.isError or "SEQUENTIAL_TEST_GROK" not in str(payload.get("text", "")):
                    raise RuntimeError("Build-first all-tools agent smoke failed")
                if payload.get("resolved_plane") != "cli" or not all(enabled.values()):
                    raise RuntimeError("default all-tools call did not remain Build-first")
                if tools_receipt.get("user_notice_required") is not True:
                    raise RuntimeError("Build-first result omitted the user disclosure receipt")
                telemetry_id = payload.get("telemetry_id")
                if not isinstance(telemetry_id, int):
                    raise RuntimeError("agent result omitted telemetry_id")
                feedback = await session.call_tool(
                    "record_benchmark_result",
                    {
                        "telemetry_id": telemetry_id,
                        "success": True,
                        "note": "release smoke marker matched",
                    },
                )
                if feedback.isError:
                    raise RuntimeError("benchmark feedback write failed")
                benchmark = _structured(await session.call_tool("benchmark_status", {}))
                callers = benchmark.get("telemetry", {}).get("callers", [])
                if not any(item.get("name") == "release-smoke" for item in callers):
                    raise RuntimeError("X-Client-ID was not attributed in benchmark telemetry")
                print("auto_all_tools=SEQUENTIAL_TEST_GROK plane=cli")

            if invoke_research:
                result, payload = await _agent_call(
                    session,
                    {
                        "task": (
                            "Find the latest official xAI Grok model announcements. "
                            "Answer with model names, announcement dates, and source URLs."
                        ),
                    },
                )
                text = str(payload.get("text") or "").strip()
                narration_prefixes = ("I'll ", "I’ll ", "Let me ", "Searching ", "Fetching ")
                if result.isError:
                    detail = " ".join(
                        str(getattr(item, "text", "")) for item in result.content
                    ).strip()
                    raise RuntimeError(
                        f"auto CLI research smoke returned an error: {detail[:300]}"
                    )
                if payload.get("resolved_plane") != "cli":
                    raise RuntimeError("auto web request did not remain on Grok Build")
                if payload.get("stop_reason") != "EndTurn":
                    raise RuntimeError("auto CLI research did not complete with EndTurn")
                if len(text) < 500 or "http" not in text:
                    raise RuntimeError("auto CLI research returned an incomplete answer")
                if text.startswith(narration_prefixes):
                    raise RuntimeError("auto CLI research leaked pre-tool narration")
                print(f"auto_web=complete plane=cli chars={len(text)} citations=present")

            if invoke_api:
                result = await session.call_tool(
                    "remote_code_execution",
                    {"prompt": "Use Python to return exactly DUAL_PLANE_API_OK."},
                )
                payload = _structured(result)
                if result.isError or "DUAL_PLANE_API_OK" not in str(payload.get("text", "")):
                    raise RuntimeError("explicit API chat smoke failed")
                print("api=DUAL_PLANE_API_OK")

            if invoke_code:
                result, payload = await _agent_call(
                    session,
                    {
                        "task": (
                            "Write a tiny Python function named launch_marker that returns "
                            "exactly the string BUILD_SPECIALIST_OK. Return code only."
                        )
                    },
                )
                orchestration = payload.get("orchestration", {})
                if result.isError or "BUILD_SPECIALIST_OK" not in str(payload.get("text", "")):
                    raise RuntimeError("automatic code-specialist smoke failed")
                if orchestration.get("route") != "code" or "build" not in str(
                    orchestration.get("specialist_model", "")
                ).lower():
                    raise RuntimeError(f"code task was not routed through Build: {orchestration}")
                print(
                    "code_route=lead_to_build "
                    f"specialist={orchestration.get('specialist_model')}"
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:4765/mcp")
    parser.add_argument("--invoke-cli", action="store_true")
    parser.add_argument("--invoke-api", action="store_true")
    parser.add_argument("--invoke-research", action="store_true")
    parser.add_argument("--invoke-code", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        smoke(
            args.url,
            args.invoke_cli,
            args.invoke_api,
            args.invoke_research,
            args.invoke_code,
        )
    )


if __name__ == "__main__":
    main()
