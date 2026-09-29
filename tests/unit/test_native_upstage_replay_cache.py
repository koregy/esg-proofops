"""Upstage checkpoint reads reuse only a successful composition of the same inputs."""

from dataclasses import dataclass, replace

import pytest
from proofops.adapters.local import native_upstage_replay_cache as cache


@dataclass(frozen=True)
class Block:
    source_id: str
    quality: str


@dataclass(frozen=True)
class Graph:
    blocks: tuple


@dataclass(frozen=True)
class Message:
    tenant_id: str
    run_id: str


@pytest.fixture
def replay(monkeypatch):
    from proofops.adapters.local import native_upstage_ocr as v1
    from proofops.adapters.local import native_upstage_ocr_widget as widget
    from proofops.adapters.local import native_widget_visibility as visibility

    monkeypatch.setattr(cache, "_compositions", type(cache._compositions)())
    monkeypatch.setattr(cache, "_bytes", 0)
    policies = {"widget": "w1", "upstage": "u1"}
    monkeypatch.setattr(
        visibility, "native_widget_visibility_policy", lambda: {"v": policies["widget"]}
    )
    monkeypatch.setattr(v1, "live_policy_for", lambda stored: {"v": policies["upstage"]})
    calls = []

    def compose(snapshot, message, native, graph, source, entries, refs):
        calls.append(message.tenant_id)
        if entries and entries[0][1].get("bad"):
            raise ValueError("NATIVE_UPSTAGE_OCR_INELIGIBLE")
        blocks = tuple(
            replace(b, quality="verified") if b.source_id == "p1" else b for b in graph.blocks
        )
        coverage = {"corroborated_source_ids": ["p1"], "source": source.decode()}
        return replace(graph, blocks=blocks), coverage, list(refs), "policy-hash"

    monkeypatch.setattr(widget, "compose_checkpoint", compose)
    return calls, policies


def _args(**changes):
    args = dict(
        snapshot={
            "native_upstage_ocr_policy": {"mode": "standard"},
            "native_upstage_ocr_widget_visibility": {"v": "w1"},
            "selected_pages": [1],
        },
        message=Message("tenant", "run"),
        native={"schema": "native_paragraph_attestation_v2"},
        graph=Graph((Block("p1", "unresolved"), Block("p2", "unresolved"))),
        source=b"pdf",
        entries=[({"request_id": "r1"}, {"text": "a"}, "receipt-sha")],
        refs=[{"request_id": "r1", "receipt_sha256": "receipt-sha"}],
    )
    args.update(changes)
    return args


def _call(args):
    return cache.compose_checkpoint_cached(*args.values())


def test_reuse_returns_equal_independent_results(replay):
    calls, _ = replay
    first = _call(_args())
    first[1]["corroborated_source_ids"].clear()
    first[2].clear()
    second = _call(_args())
    assert calls == ["tenant"]
    assert second[0].blocks == (Block("p1", "verified"), Block("p2", "unresolved"))
    assert second[1]["corroborated_source_ids"] == ["p1"]
    assert second[2] == [{"request_id": "r1", "receipt_sha256": "receipt-sha"}]
    assert second[3] == "policy-hash"


@pytest.mark.parametrize(
    "changes",
    [
        {"message": Message("other-tenant", "run")},
        {"source": b"changed pdf"},
        {"native": {"schema": "native_paragraph_attestation_v2", "x": 1}},
        {"graph": Graph((Block("p1", "unresolved"),))},
        {"entries": [({"request_id": "r1"}, {"text": "mutated"}, "receipt-sha")]},
        {"refs": [{"request_id": "r1", "receipt_sha256": "other"}]},
        {"snapshot": {"native_upstage_ocr_policy": {"mode": "standard"}, "selected_pages": [2]}},
    ],
)
def test_any_changed_input_replays(replay, changes):
    calls, _ = replay
    _call(_args())
    _call(_args(**changes))
    assert len(calls) == 2


@pytest.mark.parametrize("name", ["widget", "upstage"])
def test_changed_live_policy_replays(replay, name):
    calls, policies = replay
    _call(_args())
    policies[name] = "changed"
    _call(_args())
    assert len(calls) == 2


def test_failures_are_never_cached(replay):
    calls, _ = replay
    bad = _args(entries=[({"request_id": "r1"}, {"bad": True}, "receipt-sha")])
    for _ in range(2):
        with pytest.raises(ValueError, match="NATIVE_UPSTAGE_OCR_INELIGIBLE"):
            _call(bad)
    assert len(calls) == 2
    assert not cache._compositions


def test_entries_and_bytes_are_bounded(replay, monkeypatch):
    monkeypatch.setattr(cache, "_MAX_ENTRIES", 2)
    for index in range(4):
        _call(_args(source=b"pdf-%d" % index))
    assert len(cache._compositions) == 2
    assert cache._bytes == sum(entry[4] for entry in cache._compositions.values())
    size = next(iter(cache._compositions.values()))[4]
    monkeypatch.setattr(cache, "_MAX_BYTES", size + size // 2)
    _call(_args(source=b"pdf-4"))
    assert len(cache._compositions) == 1
    assert cache._bytes == size
    monkeypatch.setattr(cache, "_MAX_BYTES", size - 1)
    calls, _ = replay
    before = len(calls)
    _call(_args(source=b"pdf-5"))
    _call(_args(source=b"pdf-5"))
    assert len(calls) == before + 2
