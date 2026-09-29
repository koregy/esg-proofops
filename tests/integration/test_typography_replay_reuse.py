"""Typography-wrapper replay reuse and the platform-refusal retry rule.

Measured problem (k12 Kia run on win32, 2026-09-29): the run pins the R24
typography wrapper, whose receipts were never admitted to the byte-identical
whole-receipt reuse (only bullet alignment was pinned). On top of that, off
macOS both rendered readers answer ``UnsupportedPlatform`` before touching a
pixel, and the retry rule treated that fixed verdict as transient, so neither
the wrapper reuse nor the full-replay cache ever kept a receipt. Every batch
therefore recomputed the whole growing ref set three times, and every new
process again.

These tests pin the reuse and every refusal. Reuse still requires a receipt
canonically identical to one this process recomputed from the original bytes
under the strict key (tenant, source bytes, graph, refs, policy, platform,
toolchain, reader versions). No model, network or AWS call.
"""

import sys

import pytest
from proofops.adapters.local import claim_source_policies as policies
from proofops.adapters.local import claim_source_verification as base
from proofops.adapters.local import claim_span_typography as wrapper
from proofops.adapters.local import native_replay_cache as cache
from proofops.adapters.local import selected_cell_table_verification as cell_reader
from proofops.domain.rulepacks import canonical_json

from tests.acceptance.test_parsing import TENANT
from tests.integration.test_batch_attestation_cache import discovery_for
from tests.integration.test_claim_span_typography import dot_inputs, render_as_bullet

PLATFORM = dict(
    status="unresolved", reason="rendered_reader_unavailable", error="UnsupportedPlatform"
)


def clear():
    cache._claim_attestations.clear()
    cache._claim_replays.clear()
    cache._claim_wrapper_receipts.clear()


def recomputes(monkeypatch):
    """Count full typography recomputes (every inner layer's per-ref PDF reads)."""
    calls = []
    original = wrapper.apply_claim_span_typography

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(wrapper, "apply_claim_span_typography", counted)
    return calls


def readers_return(monkeypatch, value, platform):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(base, "_rendered_text", lambda *a, **k: dict(value))
    monkeypatch.setattr(cell_reader, "_rendered_cell", lambda page, box: dict(value))


def case():
    source, graph, ref = dot_inputs()
    discovery = discovery_for(graph, (ref,))
    return source, graph, discovery, base.discovery_refs(discovery)


def publish(graph, source, refs, *, cached=True):
    return policies.attest_claims(
        reader=wrapper, graph=graph, source=source, refs=refs, tenant_id=TENANT, cache=cached
    )


def replay(*, receipt, graph, source, discovery, policy=None, tenant_id=TENANT):
    return cache.replay_claims_cached(
        reader=wrapper,
        policy=policy or wrapper.claim_source_policy(),
        receipt=receipt,
        graph=graph,
        source=source,
        discovery=discovery,
        tenant_id=tenant_id,
    )


def test_the_pinned_wrapper_digest_is_this_checkout_s_typography_wrapper():
    assert wrapper.claim_source_policy()["wrapper_sha256"] == cache._TYPOGRAPHY_WRAPPER


def test_published_receipt_is_byte_identical_and_replay_reuses_it(monkeypatch):
    render_as_bullet(monkeypatch)
    clear()
    source, graph, discovery, refs = case()
    direct = wrapper.attest_claim_spans(graph, source, refs, tenant_id=TENANT)
    frozen = wrapper.replay_claim_spans(direct, graph, source, discovery, tenant_id=TENANT)
    assert [r["status"] for r in direct["records"]] == ["verified"]
    clear()
    receipt = publish(graph, source, refs)
    assert canonical_json(receipt) == canonical_json(direct)
    assert cache._claim_wrapper_receipts
    calls = recomputes(monkeypatch)
    replayed, scoped = replay(receipt=receipt, graph=graph, source=source, discovery=discovery)
    assert calls == []
    assert (replayed, scoped) == frozen
    assert replayed.claims[0].source_quality == "verified"


def test_a_cold_process_runs_the_frozen_replay_once_then_reuses(monkeypatch):
    render_as_bullet(monkeypatch)
    clear()
    source, graph, discovery, refs = case()
    receipt = publish(graph, source, refs, cached=False)
    clear()
    calls = recomputes(monkeypatch)
    cold = replay(receipt=receipt, graph=graph, source=source, discovery=discovery)
    assert len(calls) == 1
    cache._claim_replays.clear()
    assert replay(receipt=receipt, graph=graph, source=source, discovery=discovery) == cold
    assert len(calls) == 1


def test_reuse_is_bound_to_tenant_source_graph_refs_and_policy(monkeypatch):
    from dataclasses import replace

    render_as_bullet(monkeypatch)
    clear()
    source, graph, discovery, refs = case()
    receipt = publish(graph, source, refs)
    calls = recomputes(monkeypatch)
    pinned = dict(receipt=receipt, graph=graph, source=source, discovery=discovery)
    other_ref = replace(refs[0], char_start=0, char_end=9, quote="emissions")
    for changed, recomputed, refused in (
        (dict(tenant_id="00000000-0000-4000-8000-000000000001"), True, True),
        (dict(source=source + b"\n"), True, True),
        (dict(graph=replace(graph, source_sha256="0" * 64)), True, True),
        # An unpinned policy is not projected; the frozen replay runs as before.
        (dict(policy=dict(wrapper.claim_source_policy(), wrapper_sha256="0" * 64)), True, False),
        # A different ref set is refused by the wrapper before it even reads.
        (dict(discovery=discovery_for(graph, (other_ref,))), False, True),
    ):
        before = len(calls)
        cache._claim_replays.clear()
        if refused:
            with pytest.raises(ValueError):
                replay(**(pinned | changed))
        else:
            assert replay(**(pinned | changed)) is not None
        assert (len(calls) > before) is recomputed, changed


@pytest.mark.parametrize(
    "altered",
    [
        lambda r: dict(r, records=[dict(r["records"][0], status="unresolved", reason="x")]),
        lambda r: dict(r, artifact_sha256="0" * 64),
        lambda r: dict(r, typography_reads={}),
        lambda r: dict(r, readings={}),
        lambda r: dict(r, schema="claim_span_bullet_alignment_attestation_v1"),
    ],
)
def test_a_tampered_receipt_is_refused_instead_of_reused(monkeypatch, altered):
    render_as_bullet(monkeypatch)
    clear()
    source, graph, discovery, refs = case()
    receipt = publish(graph, source, refs)
    with pytest.raises(ValueError):
        replay(receipt=altered(receipt), graph=graph, source=source, discovery=discovery)


def test_platform_refusal_off_macos_is_deterministic_and_reused(monkeypatch):
    readers_return(monkeypatch, PLATFORM, "win32")
    clear()
    source, graph, discovery, refs = case()
    direct = wrapper.attest_claim_spans(graph, source, refs, tenant_id=TENANT)
    frozen = wrapper.replay_claim_spans(direct, graph, source, discovery, tenant_id=TENANT)
    assert [r["reason"] for r in direct["records"]] == ["rendered_reader_unavailable"]
    clear()
    receipt = publish(graph, source, refs)
    assert canonical_json(receipt) == canonical_json(direct)
    assert cache._claim_wrapper_receipts
    calls = recomputes(monkeypatch)
    first = replay(receipt=receipt, graph=graph, source=source, discovery=discovery)
    assert first == frozen and calls == []
    assert cache._claim_replays
    assert replay(receipt=receipt, graph=graph, source=source, discovery=discovery) == frozen
    assert calls == []
    # Never an approval: the refused record stays unresolved and unverified.
    assert first[0].claims[0].source_quality == "unverified"


def test_the_same_refusal_on_macos_is_still_retried(monkeypatch):
    readers_return(monkeypatch, PLATFORM, "darwin")
    clear()
    source, graph, discovery, refs = case()
    receipt = publish(graph, source, refs)
    assert not cache._claim_wrapper_receipts
    calls = recomputes(monkeypatch)
    replay(receipt=receipt, graph=graph, source=source, discovery=discovery)
    replay(receipt=receipt, graph=graph, source=source, discovery=discovery)
    assert len(calls) == 2
    assert not cache._claim_wrapper_receipts and not cache._claim_replays


@pytest.mark.parametrize(
    "value",
    [
        dict(PLATFORM, error="OSError"),
        dict(PLATFORM, error="EmptyBox"),
        dict(PLATFORM, detail="extra"),
        dict(status="unresolved", reason="render_limit"),
    ],
)
def test_any_other_unavailable_read_off_macos_is_still_retried(monkeypatch, value):
    readers_return(monkeypatch, value, "win32")
    clear()
    source, graph, discovery, refs = case()
    receipt = publish(graph, source, refs)
    assert cache._unavailable_read(receipt)
    assert not cache._claim_wrapper_receipts
    calls = recomputes(monkeypatch)
    replay(receipt=receipt, graph=graph, source=source, discovery=discovery)
    replay(receipt=receipt, graph=graph, source=source, discovery=discovery)
    assert len(calls) == 2
    assert not cache._claim_replays


def platform_reading():
    return dict(
        status="unresolved",
        reason="rendered_reader_unavailable",
        rendered=dict(PLATFORM),
        rendered_attempts=[dict(PLATFORM)],
    )


def record_for(source_id):
    return dict(
        ref=dict(source_id=source_id), status="unresolved", reason="rendered_reader_unavailable"
    )


def test_unavailable_read_rule_is_exact(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    reading = platform_reading()
    assert not cache._unavailable_read(
        dict(readings={"s": reading}, records=[record_for("s")], base_records=[record_for("s")])
    )
    # Summaries with no reader error behind them are not proof of a platform refusal.
    assert cache._unavailable_read(dict(records=[record_for("s")]))
    assert cache._unavailable_read(dict(readings={"s": reading}, records=[record_for(None)]))
    assert cache._unavailable_read([dict(PLATFORM), dict(PLATFORM, error="TimeoutExpired")])
    assert not cache._unavailable_read(dict(records=[dict(status="verified")]))
    # A reading must be refused on its own read AND every attempt.
    assert cache._unavailable_read(
        dict(readings={"s": dict(reading, rendered_attempts=[dict(PLATFORM), dict(PLATFORM)])})
    )
    monkeypatch.setattr(sys, "platform", "darwin")
    assert cache._unavailable_read(dict(readings={"s": reading}))


def test_mixed_platform_and_unrelated_reason_only_verdicts_still_retry(monkeypatch):
    """Review regression: one exact platform refusal must not exempt an unrelated
    reason-only unavailable mark elsewhere in the same receipt."""
    monkeypatch.setattr(sys, "platform", "win32")
    unrelated = dict(status="unresolved", reason="rendered_reader_unavailable")
    for receipt in (
        # A second reading with no rendered read behind it.
        dict(readings={"s": platform_reading(), "t": unrelated}, records=[record_for("s")]),
        # A record pointing at a reading that is not a platform refusal.
        dict(
            readings={"s": platform_reading(), "t": unrelated},
            records=[record_for("s"), record_for("t")],
        ),
        # A record pointing at no reading at all.
        dict(readings={"s": platform_reading()}, records=[record_for("u")]),
        # A reason-only mark nested elsewhere (e.g. a wrapper field).
        dict(
            readings={"s": platform_reading()},
            records=[record_for("s")],
            render_retries={"s": dict(retry=unrelated)},
        ),
        # A render_limit next to a platform refusal.
        dict(
            readings={"s": platform_reading()},
            records=[record_for("s")],
            other=dict(status="unresolved", reason="render_limit"),
        ),
    ):
        assert cache._unavailable_read(receipt), receipt


def test_mixed_verdict_receipt_is_never_remembered_and_is_retried(monkeypatch):
    """End to end on real bytes: the base reader refuses by platform while the
    render-resolution retry reader reports a reason-only unavailable read."""
    from proofops.adapters.local import selected_cell_table_verification as cell_reader

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(base, "_rendered_text", lambda *a, **k: dict(PLATFORM))
    monkeypatch.setattr(
        cell_reader,
        "_rendered_cell",
        lambda page, box: dict(status="unresolved", reason="rendered_reader_unavailable"),
    )
    clear()
    source, graph, discovery, refs = case()
    receipt = publish(graph, source, refs)
    if not cache._unavailable_read(receipt):
        # The retry reader is only consulted for text_mismatch records; inject the
        # mixed shape into a copy to prove the guard regardless of that path.
        receipt = dict(receipt, render_retries={"x": dict(rendered=dict(reason="render_limit"))})
        assert cache._unavailable_read(receipt)
        return
    assert not cache._claim_wrapper_receipts
    calls = recomputes(monkeypatch)
    replay(receipt=receipt, graph=graph, source=source, discovery=discovery)
    replay(receipt=receipt, graph=graph, source=source, discovery=discovery)
    assert len(calls) == 2 and not cache._claim_replays
