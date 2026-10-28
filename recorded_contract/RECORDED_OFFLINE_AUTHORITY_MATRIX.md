# Authority matrix — proposed allowlist criteria

No named local provider or capture implementation is approved by this document. Registry entries must pin authority ID, allowed receipt kinds, decision system, verifier/version hash, trust anchor, clock source/uncertainty policy, retention/integrity mechanism and revocation policy. Policy hash binds the registry snapshot. Verification is offline; unavailable trust material means UNKNOWN.

| Evidence | Permitted conclusion | Required verification | Not sufficient for |
|---|---|---|---|
| Source-signed event record | Source event timestamp | Trusted issuer/key, exact payload binding, clock bound, correct revision | Local availability |
| Contemporaneous append-only capture journal | captured_at | Exact bytes/hash, monotonic stream sequence, clock health record, tamper-evident trusted anchor and capture identity | Computation completion or delivery |
| Decision-system durable-ready receipt | available_at | Original receipt bound to version/hash and decision system; trusted readiness barrier, clock/uncertainty, integrity chain | Earlier availability before receipt |
| Original derivation completion journal | Derived completion | Exact output/dependency/engine/config hashes; verified readiness/clock chain | Availability of unlisted inputs |
| Original scenario establishment journal | scenario_established_at | Exact version, hypothesis lifecycle and full dependency lineage; trusted time | Automatic permissions or alert generation |
| Signed timestamp / external append-only anchor | Bytes existed no later than conservative anchored time | Signature/anchor verified and payload hash exact | Local decision usability without ready receipt |
| Candle close / broker event time | Market event claim | Offset/calendar semantics independently checked | Receipt, scenario formation or availability |
| CSV snapshot_utc | Candidate capture-time claim | Needs binding receipt and audited capture implementation | Automatic KNOWN availability |
| Filesystem mtime / ZIP date | File metadata only | No historical authority | Any historical availability claim |
| Current import/replay clock | Current audit execution | Current clock provenance | Historical readiness/establishment |
| User description / screenshot | Context, structural hypothesis | Preserve attribution, no inferred clock | Timely algorithm detection |
| SHA-256 alone | Byte identity | Rehash exact bytes | Timestamp, origin authenticity or completeness |

Receipt binding must distinguish market source from capture service and decision consumer. Two feeds with shared upstream are not independent by declaration. Unverified independence remains UNKNOWN. Raw captures, account identifiers and recipients remain private and are not embedded in this contract package.

Audit checklist for future dataset admission: capture code/version, offset evidence, clock drift history, append integrity/anchors, immutable retention, complete stream sequences, full required role coverage, derivation/establishment receipts, correction history. If these are absent the dataset may support descriptive retrospective analysis only. This is a technical admission decision; no live data acquisition is implied.
