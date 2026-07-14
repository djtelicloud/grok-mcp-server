"""Provider-neutral transport contract + deterministic mock.

The harvester speaks only this typed interface. The real implementation is
the UniGrok MCP gateway, which owns every credential; until that interface
lands, :class:`MockTransport` is the only concrete transport, and
:func:`live_transport` refuses to construct anything else.

This module — like the whole package — performs no network I/O, reads no
environment variables, and opens no credential files. Provider and model
catalogs are *discovered at runtime* through :meth:`Transport.discover`,
never assumed from product names.
"""

from __future__ import annotations

import hashlib
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

TransportStatusLiteral = Literal[
    "OK", "TIMEOUT", "EMPTY", "MALFORMED", "REFUSED", "ERROR"
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderModel(StrictModel):
    """One runtime-discovered donor/judge identity (provider-neutral key)."""

    donor_key: str  # neutral handle used everywhere model-visible text exists
    provider: str  # provenance only; never enters training text
    model_id: str
    plane: str  # e.g. "api" | "cli" | "mock"


class ProviderReceipt(StrictModel):
    """Validated provenance for one call. Kept out of model-visible text."""

    provider: str
    model_id: str
    plane: str
    runtime: str


class TransportRequest(StrictModel):
    work_key: str
    donor_key: str
    objective: str  # model-visible objective text only
    temperature: float = Field(ge=0.0, le=2.0)
    top_p: float = Field(gt=0.0, le=1.0)
    seed: int = Field(ge=0)
    max_tokens: int = Field(gt=0)


class TransportResult(StrictModel):
    """Outcome of one provider call.

    ``transport_status`` is deliberately independent from any semantic
    verdict: a TIMEOUT/EMPTY/MALFORMED/ERROR result is an infrastructure
    fact, never an answer, and never a preference negative.
    """

    work_key: str
    transport_status: TransportStatusLiteral
    text: str = ""
    receipt: ProviderReceipt | None = None


class Transport(Protocol):
    def discover(self) -> tuple[ProviderModel, ...]:
        """Runtime catalog of configured donors (exact identities)."""
        ...

    def call(self, request: TransportRequest) -> TransportResult:
        """Execute one bounded provider call."""
        ...


class MockTransport:
    """Deterministic, network-free transport for mock harvesting.

    Responses come from an injected cassette: ``{(donor_key, root_id_hint):
    [entry, ...]}`` where each entry is ``{"status": ..., "text": ...}``
    consumed round-robin by sample index derived from the work key. When no
    cassette entry matches, a deterministic synthetic response is derived
    from the work key, so identical inputs always produce identical outputs.
    """

    def __init__(
        self,
        catalog: tuple[ProviderModel, ...],
        cassette: dict[str, list[dict[str, str]]] | None = None,
    ) -> None:
        self._catalog = tuple(catalog)
        self._cassette = {k: list(v) for k, v in (cassette or {}).items()}
        self._cursor: dict[str, int] = {}
        self.calls: list[TransportRequest] = []

    def discover(self) -> tuple[ProviderModel, ...]:
        return self._catalog

    def call(self, request: TransportRequest) -> TransportResult:
        self.calls.append(request)
        model = next(
            (m for m in self._catalog if m.donor_key == request.donor_key), None
        )
        if model is None:
            return TransportResult(
                work_key=request.work_key, transport_status="REFUSED"
            )
        receipt = ProviderReceipt(
            provider=model.provider,
            model_id=model.model_id,
            plane=model.plane,
            runtime="mock",
        )
        entries = self._cassette.get(request.donor_key)
        if entries:
            index = self._cursor.get(request.donor_key, 0)
            entry = entries[index % len(entries)]
            self._cursor[request.donor_key] = index + 1
            status = entry.get("status", "OK")
            return TransportResult(
                work_key=request.work_key,
                transport_status=status,  # type: ignore[arg-type]
                text=entry.get("text", "") if status == "OK" else "",
                receipt=receipt,
            )
        synthetic = hashlib.sha256(
            f"{request.work_key}|{request.donor_key}|{request.seed}".encode()
        ).hexdigest()
        return TransportResult(
            work_key=request.work_key,
            transport_status="OK",
            text=f"mock-answer-{synthetic[:16]}",
            receipt=receipt,
        )


def live_transport(*_args: object, **_kwargs: object) -> Transport:
    """Placeholder for the provider-neutral UniGrok MCP transport.

    The interface is typed above; the implementation is deliberately absent
    until the UniGrok MCP contract lands. No direct vendor client will ever
    be created here — the MCP server owns credentials.
    """
    raise NotImplementedError(
        "live transport requires the provider-neutral UniGrok MCP interface, "
        "which is not landed; only MockTransport exists. Direct vendor "
        "clients are forbidden by the provider boundary."
    )
