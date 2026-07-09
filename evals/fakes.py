# evals/fakes.py
# Shared fake xAI SDK doubles used by BOTH the offline eval runner and the
# pytest suite (tests/test_utils.py imports these instead of keeping its own
# mock copies). Plain Python objects — no unittest.mock — so the same fakes
# run identically inside pytest and under `python -m evals run`.
#
# The injection point mirrors the tests' established pattern: everything in
# src/utils.py reaches the SDK through get_xai_client(), so patching
# src.utils.get_xai_client (or assigning src.utils._client) with a FakeClient
# makes AgentLoop, the fast path, and the reflection reviewer all replay
# scripted responses with zero network.

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional


class FakeUsage:
    """Usage double matching the attributes src/utils.py reads."""

    def __init__(self, prompt_tokens: int = 10, completion_tokens: int = 20,
                 reasoning_tokens: int = 0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.reasoning_tokens = reasoning_tokens


class FakeResponse:
    """Response double covering the full surface src/utils.py touches:
    content, tool_calls, usage, cost_usd, id, citations, inline_citations,
    tool_outputs. Attributes are plain and assignable so tests can adjust
    individual fields after construction."""

    def __init__(
        self,
        content: str = "",
        tool_calls: Optional[list] = None,
        usage: Optional[FakeUsage] = None,
        cost_usd: Optional[float] = 0.001,
        citations: Optional[List[str]] = None,
        inline_citations: Optional[list] = None,
        response_id: Optional[str] = None,
    ):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage
        self.cost_usd = cost_usd
        self.citations = list(citations or [])
        self.inline_citations = list(inline_citations or [])
        self.tool_outputs: list = []
        self.id = response_id


def make_response(
    content: str = "",
    tool_calls=None,
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    cost_usd: float = 0.001,
    reasoning_tokens: int = 0,
    citations: Optional[List[str]] = None,
    inline_citations: Optional[list] = None,
    response_id: Optional[str] = None,
) -> FakeResponse:
    """Build a fake xAI SDK response object (AgentLoop/fast-path compatible)."""
    return FakeResponse(
        content=content,
        tool_calls=tool_calls,
        usage=FakeUsage(prompt_tokens, completion_tokens, reasoning_tokens),
        cost_usd=cost_usd,
        citations=citations,
        inline_citations=inline_citations,
        response_id=response_id,
    )


def make_tool_call(name: str, arguments: Any = None, call_id: str = "call-1"):
    """Fake tool-call double matching tc.id / tc.function.name /
    tc.function.arguments (arguments serialized to a JSON string, as the SDK
    delivers them)."""
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments or {})
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


class FakeChat:
    """Chat double whose .messages accumulates appends, mirroring the SDK
    contract the escalation rebuild relies on: the conversation lives on the
    chat's message list and each entry can be re-appended onto a new chat.

    Responses come from either this chat's own queue (when constructed with
    an explicit list) or the owning FakeClient's shared queue — the shared
    queue is what lets a cassette script span chat rebuilds (self-escalation)
    and reviewer chats (thinking mode) in one ordered list.
    """

    def __init__(self, responses: Optional[list] = None, verdicts: Optional[list] = None,
                 client: Optional["FakeClient"] = None):
        self._responses = list(responses) if responses is not None else None
        self._verdicts = list(verdicts) if verdicts is not None else None
        self._client = client
        self.messages: list = []
        self.sample_count = 0
        # Number of messages appended before the FIRST sample — the eval
        # runner's structural probe that session history really got replayed.
        self.appends_before_first_sample: Optional[int] = None

    def append(self, message):
        self.messages.append(message)
        return self

    def _next_response(self):
        if self._responses is not None:
            if not self._responses:
                raise AssertionError("no queued responses left on this chat")
            return self._responses.pop(0)
        if self._client is not None:
            return self._client.next_response()
        raise AssertionError("FakeChat has no response source (queue or client)")

    def sample(self):
        if self.appends_before_first_sample is None:
            self.appends_before_first_sample = len(self.messages)
        self.sample_count += 1
        return self._next_response()

    def parse(self, shape):
        """Structured-output double for chat.parse(shape) — pops the next
        scripted verdict dict and instantiates the caller's shape with it.
        With no scripted verdict this raises, which _reflect_on_answer treats
        as 'reviewer unavailable' (the answer is accepted) — still fully
        deterministic."""
        spec = None
        if self._verdicts is not None:
            if self._verdicts:
                spec = self._verdicts.pop(0)
        elif self._client is not None:
            spec = self._client.next_verdict()
        if spec is None:
            raise RuntimeError("no scripted parse verdict left in cassette")
        usage = spec.get("usage") or {}
        response = make_response(
            content="",
            prompt_tokens=int(usage.get("prompt_tokens", 5)),
            completion_tokens=int(usage.get("completion_tokens", 5)),
            cost_usd=float(spec.get("cost_usd", 0.0005)),
        )
        fields = {k: v for k, v in spec.items() if k not in ("usage", "cost_usd")}
        return response, shape(**fields)


class FakeClient:
    """Client double handing out chats and recording every create() kwargs.

    Two modes:
      - script mode (default): FakeClient(responses=[...], verdicts=[...]) —
        each chat.create() returns a fresh FakeChat drawing from the SHARED
        response/verdict queues in order.
      - prebuilt mode: FakeClient(chats=[chat1, chat2]) — hands out the
        queued chats verbatim (each with its own response queue), matching
        the original escalation-test doubles.
    """

    def __init__(self, responses: Optional[list] = None, verdicts: Optional[list] = None,
                 chats: Optional[list] = None):
        self._responses = list(responses or [])
        self._verdicts = list(verdicts or [])
        self._prebuilt = list(chats) if chats is not None else None
        self.create_calls: List[Dict[str, Any]] = []
        self.chats: List[FakeChat] = []
        self.chat = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.create_calls.append(kwargs)
        if self._prebuilt is not None:
            if not self._prebuilt:
                raise AssertionError("no queued chats left")
            chat = self._prebuilt.pop(0)
        else:
            chat = FakeChat(client=self)
        self.chats.append(chat)
        return chat

    def next_response(self):
        if not self._responses:
            raise AssertionError("cassette exhausted: no scripted responses left")
        return self._responses.pop(0)

    def next_verdict(self) -> Optional[dict]:
        if not self._verdicts:
            return None
        return self._verdicts.pop(0)

    @property
    def responses_remaining(self) -> int:
        return len(self._responses)


def response_from_spec(spec: Dict[str, Any]) -> FakeResponse:
    """Build a FakeResponse from a cassette response spec.

    Spec shape (all keys optional except content):
      {"content": str,
       "tool_calls": [{"id": str, "name": str, "arguments": dict|str}],
       "usage": {"prompt_tokens": int, "completion_tokens": int},
       "cost_usd": float,
       "citations": [str],
       "id": str}
    """
    tool_calls = [
        make_tool_call(
            name=str(tc.get("name") or "unknown"),
            arguments=tc.get("arguments"),
            call_id=str(tc.get("id") or f"call-{idx + 1}"),
        )
        for idx, tc in enumerate(spec.get("tool_calls") or [])
    ]
    usage = spec.get("usage") or {}
    return make_response(
        content=str(spec.get("content") or ""),
        tool_calls=tool_calls,
        prompt_tokens=int(usage.get("prompt_tokens", 10)),
        completion_tokens=int(usage.get("completion_tokens", 20)),
        cost_usd=float(spec.get("cost_usd", 0.001)),
        citations=list(spec.get("citations") or []),
        response_id=spec.get("id"),
    )


def client_from_cassette(script: Dict[str, Any]) -> FakeClient:
    """Build a script-mode FakeClient from a cassette entry:
    {"responses": [response spec, ...], "verdicts": [verdict spec, ...]}."""
    responses = [response_from_spec(spec) for spec in (script.get("responses") or [])]
    verdicts = list(script.get("verdicts") or [])
    return FakeClient(responses=responses, verdicts=verdicts)
