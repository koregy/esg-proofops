# Export bundle payload contract

Status: implemented locally in `packages/proofops/application/exports.py` and
`packages/proofops/adapters/local/export_store.py`. No database migration, no route
change, no API schema change, no change to `MAX_EXPORT_BYTES` (still 32 MiB in both
`proofops.application.exports` and the local capture guard). Verified against the
frozen NAVER 327-claim / 29-published-tag review state over real local HTTP, with zero
model calls. AWS validation remains **not_run**.

## 1. Problem this closes

Exporting the frozen NAVER partial-tag review state failed with HTTP 409
`EXPORT_SIZE_LIMIT` before any format was rendered. Measured on an isolated copy of
that state with the product canonical encoder:

| quantity | bytes |
|---|---|
| 29 `tag_revision` records, sum of `canonical_json(tag)` | 29,805,414 |
| the `inputs` packet inside those tags | 29,684,101 |
| `capture()` revision records with `original_inputs` inlined a second time | 59,490,762 |
| `capture()` revision records with the identical packet stored once | 29,806,777 |
| `MAX_EXPORT_BYTES` | 33,554,432 |

`capture()` built `dict(tag=tag, decision=decision, original_inputs=inputs)` where
`inputs` resolves to `tag["inputs"]` whenever the current tag carries its own input
packet, so the largest payload in the export was serialised twice. For all 29 records
the two packets were byte-equal under `canonical_json`. Removing the duplicate is enough
to pass capture and freeze; the 29.8 MiB manifest plus the three rendered reports still
did not fit a `ZIP_STORED` bundle inside the same 32 MiB artifact cap.

This is a payload-encoding gap. No evidence, source reference, replica hash, revision,
grade or review state is dropped, truncated or summarised.

## 2. `manifest.revision_records` encodings

`manifest.revision_records_encoding` declares how each record in
`manifest.revision_records` stores the revision-1 input packet.

| value | meaning |
|---|---|
| absent | original v1 layout (`inline_original_inputs_v1`): `original_inputs` is always inline |
| `shared_original_inputs_v2` | `original_inputs` is inline **unless** `original_inputs_ref` is present |

A v2 record carries exactly one of:

- `original_inputs`: the packet verbatim, byte-for-byte as captured; or
- `original_inputs_ref: "tag.inputs"`: the packet is byte-identical to
  `record["tag"]["inputs"]` and is therefore stored once.

`encode_revision_record(tag, decision, inputs)` emits the reference **only** when
`canonical_json(tag["inputs"]) == canonical_json(inputs)`. Unequal packets are always
both preserved. A v2 record stays inline whenever the current tag has no embedded
`inputs` of its own — which is the reviewed-tag case where the packet comes from
revision 1 — so v2 is a mixed encoding by design, not an all-reference one.

### Version dispatch

`decode_revision_record(record, *, encoding=REVISION_RECORDS_V1)` defaults to the legacy
encoding, so a reader that forgets the field cannot silently accept a v2 reference.
Callers read the encoding off the manifest they are decoding:

```python
encoding = manifest.get("revision_records_encoding", REVISION_RECORDS_V1)
inline = {cid: decode_revision_record(r, encoding=encoding)
          for cid, r in manifest["revision_records"].items()}
```

`canonical_json(decode_revision_record(v2_record, encoding=REVISION_RECORDS_V2))` equals
the canonical bytes of the v1 record the previous code would have written, so the decoder
is a pure re-inlining step and never reshapes the record.

### Report-level source receipt references

The optional `manifest.source_receipts_encoding` declares the cross-record source receipt
table. When present, `shared_source_receipts_sha256_v1` means
`manifest.source_receipts` maps each canonical content SHA-256 to one complete receipt.
Every `tag.report_level_review[]` entry that carried `source_receipt` instead carries
`source_receipt_ref: "sha256:<digest>"`. Identical canonical receipts across any number of
claim revisions therefore occupy one table entry in the snapshot and ZIP manifest.

The encoder checks the receipt's own `artifact_sha256`, and checks its tenant,
document-version, parse-manifest, and source SHA-256 against both the enclosing revision
and manifest identity before emitting a reference. The decoder requires the declared
version, resolves every reference, checks the canonical receipt hash and source identity,
rejects missing, extra, inline-plus-reference, and unreferenced table entries, and returns
the original inline revision records without mutating the compact manifest. Export
rendering runs the same validation before writing the ZIP. Tag revisions remain immutable;
deduplication is only an export-snapshot encoding.

Snapshots written before this field existed keep their inline receipts and need no
rewriting. Absence of `source_receipts_encoding` means no receipt references are valid;
unknown encodings are rejected.

Every refusal is a `ValueError`:

| input | outcome |
|---|---|
| `encoding` not v1 or v2 | rejected (unknown encoding is never guessed) |
| record is not an object | rejected |
| `original_inputs` missing and no reference | rejected |
| `original_inputs_ref` present under v1 | rejected |
| `original_inputs_ref` present with an explicit `None` | rejected |
| reference value other than `"tag.inputs"` | rejected |
| both `original_inputs_ref` and `original_inputs` present | rejected |
| reference present but `tag` is not an object, or `tag.inputs` is missing, null, or not an object | rejected |

Backward compatibility: every already-frozen `export_snapshot` and `export_artifact`
lacks `revision_records_encoding`, is therefore v1, and still decodes unchanged. Older
snapshots with inline report-level receipts also lack `source_receipts_encoding` and remain
readable as-is. `build_report_model` does not use `revision_records` or the shared receipt
table, so JSON/CSV/HTML report content is unchanged; export validation expands and checks
the preserved revision records before rendering. No API request or response model, OpenAPI
path or JSON Schema changes; the only external change is the documented versioned receipt
encoding inside the archived `manifest.json`.

## 3. Shared search coverage encoding (v1)

New manifests may declare `search_coverage_encoding: "shared_search_coverage_sha256_v1"`.
`manifest.search_coverages` maps the SHA-256 of each complete original canonical JSON
`search_coverage` value to `{scope, value}`. `scope` has exactly `tenant_id`,
`document_version_id`, `parse_manifest_id`, and `source_sha256`; `value` retains all
coverage fields except `source_quality` and `quality_issues`. Their generated references
live in `value.__shared_coverage__.refs`, keyed by the original field name with values
`"sha256:<digest>"`. The envelope is always present, even when `refs` is empty.
Any original `__shared_coverage__` field (including null) is escaped as the envelope's
`original_value` and restored verbatim on decode. Original `source_quality_ref` and
`quality_issues_ref` fields remain ordinary data and are never consumed as references.
The envelope permits only `refs` and optional `original_value`; reference keys must be
`source_quality` or `quality_issues`. This corrects the unreleased R56 v1 draft shape;
pre-fix experimental shared-coverage manifests must be re-exported for this reader.
Legacy inline manifests remain compatible; no stored revision or DB migration is needed.
A packet's `search_coverage` is replaced
by `search_coverage_ref: "sha256:<digest>"`. This applies independently to
`inputs.original_packet` and `inputs.packet` in both `tag.inputs` and inline
`original_inputs`; an existing `original_inputs_ref` continues to point to `tag.inputs`.

`manifest.coverage_documents` maps each canonical JSON SHA-256 of a `source_quality`
object or `quality_issues` array to `{scope, value}`. The same document scope is checked
against the manifest and each referring packet. The two tables and their references are
export-only; the original tag, input, packet, receipt and decision revisions are immutable.
Decoding restores inner document values before verifying the complete coverage digest,
then restores each packet. It validates one revision at a time and retains only the
bounded tables and returned records. The capture guard remains 33,554,432 raw canonical
JSON bytes; no field is omitted or summarized and no compressed size substitutes for it.

Missing, malformed, hash-mismatched, wrong-scope, inline-plus-reference, and unreferenced
entries or references fail with `ValueError`; export building maps this to
`EXPORT_INTEGRITY_FAILED`. Without the encoding field, both tables and all coverage
references are forbidden, while inline legacy coverage and all earlier manifest versions
still decode. Unknown encoding versions fail closed. Rollback must retain this decoder
for new ZIP readers or re-export affected runs; existing snapshots, revisions, receipts,
and downloadable ZIP bytes are never rewritten.

## 4. Bundle compression and what is actually bounded

`build_export` writes the ZIP with `ZIP_DEFLATED` (stdlib `zipfile`). `ZipInfo()` defaults
to `ZIP_STORED` and overrides the `ZipFile` compression, so `compress_type` is set on each
`ZipInfo` explicitly; the fixed `ZipInfo` timestamp `1980-01-01 00:00:00` and the member
names and member set are unchanged, keeping the archive deterministic. `manifest_sha256`
is still the digest of the uncompressed canonical `manifest.json` bytes, not of the
archive member.

The bound is stated precisely, because deflate is not guaranteed to shrink anything and
adds framing overhead on incompressible input:

- The bundle holds at most four members: `manifest.json` plus the one to three requested
  report formats.
- The pre-write guard `stream.tell() + len(content) > MAX_EXPORT_BYTES` compares the raw
  bytes of the member about to be written against the position already reached in the
  archive. It caps each individual raw member at 32 MiB and rejects early; it is **not** a
  proof about the finished archive.
- The finished archive is measured directly: `len(content) > MAX_EXPORT_BYTES` rejects the
  export, so the stored artifact is always ≤ 32 MiB.
- Because `stream.tell()` now advances by compressed bytes, the **sum of raw members can
  exceed 32 MiB** while each raw member and the archive stay within it. In the measured
  NAVER export the raw members total 32,378,734 B and the archive is 12,978,553 B.
- The captured snapshot is independently capped at 32 MiB by the unchanged `capture()` and
  `freeze()` guards, so peak memory stays one canonical manifest buffer, one rendered
  report buffer and one archive buffer.

No decompression bound is promised or needed, because the product never decompresses an
export artifact: `authorize_download` and `content` verify `artifact_sha256` over the raw
bytes and serve them. Legacy `ZIP_STORED` artifacts are immutable stored bytes and are
still served untouched; readers open both `ZIP_STORED` and `ZIP_DEFLATED` archives.

## 5. Measured result

Real local HTTP on `.local/r24-export-size-probe`, an isolated copy of the read-only
frozen state `.local/r24-naver-partial-tag`, run `5b5445d9-947c-43fb-90a1-0e403c521f8f`,
`formats = [json, csv, html]`, `allow_partial = true`, fresh idempotency key:

- before: `POST /v1/runs/{run}/exports` → 409 `EXPORT_SIZE_LIMIT` (206.1 s)
- after: → 202 `state=ready`, `partial=true` (213.3 s), download ticket → 200

| check | value |
|---|---|
| artifact | 12,978,553 B ≤ 33,554,432 B |
| members | `manifest.json`, `report.json`, `report.csv`, `report.html`, `compress_type` 8 on all four |
| member timestamps | `1980-01-01 00:00:00` on all four (deterministic) |
| raw members total (may exceed the cap by design) | 32,378,734 B |
| largest raw member (`manifest.json`) | 29,839,256 B ≤ 33,554,432 B |
| `report.json` claims / `report.csv` data rows | 327 / 327 |
| `manifest.claim_revision_refs` / `coverage.claims_discovered` | 327 / 327 |
| `manifest.revision_records` | 29, `shared_original_inputs_v2` |
| `manifest_sha256` | equals sha256 of the archived `manifest.json` bytes |
| download ticket sha256 | equals sha256 of the served artifact |
| decoded record vs stored `tag_revision` row | 29/29 byte-identical `tag`, `original_inputs` and full canonical record |
| v1 dispatch on those v2 records | 29/29 refused with `ValueError` |
| external reader | `unzip -t` reports no errors |
| preserved revision rows (`tag_revision`, `decision_revision`, `review_*`, `claim_head`) | byte-unchanged |
| read-only frozen state `.local/r24-naver-partial-tag` | byte-unchanged |

Evidence: `outputs/agent-results/R24/export-size/` (`export_size_probe.py`,
`probe-before.json`, `probe-after.json`, `strict-decoder-recheck.json`,
`naver-partial-tag-export-after.zip`).

## 6. Rollback

Revert the two product files. Artifacts frozen in either direction stay byte-identical and
downloadable, because the download path never parses or decompresses the archive.

Reader compatibility after a revert is asymmetric and must be handled deliberately:

- v1 exports written before or after the revert: readable by both code versions.
- v2 exports written while this change was active: `manifest.revision_records_encoding`
  is `shared_original_inputs_v2` and some records carry `original_inputs_ref`. The
  reverted code has no `decode_revision_record`, so such a record would appear to have no
  `original_inputs`. Either keep `decode_revision_record` (it is a pure function with no
  dependency on the rest of the change) when reverting, or re-export the affected runs.
  The declared encoding field is what makes those exports identifiable rather than
  silently misread.
- exports with `source_receipts_encoding=shared_source_receipts_sha256_v1` contain
  `source_receipt_ref` entries and a manifest-level `source_receipts` table. Keep the
  receipt decoder/validator from `exports.py` when reverting, or re-export affected runs
  before removing it. Stored tag revisions and already-frozen ZIPs remain untouched.
