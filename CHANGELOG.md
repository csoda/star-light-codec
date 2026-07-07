# Changelog

## Unreleased

- Added LLM transport capsules for metadata-only model handoff.
- Added `hydrate` support for full, byte-range, and capsule chunk output.
- Documented the rule that compressed bytes are opaque to LLMs.
- Clarified that codec formats, schemas, interoperability specs, fixtures, test
  vectors, and benchmark result data are CC0-1.0.
- Added a synthetic transport benchmark for raw bytes, gzip, gzip+base64, SLB1,
  capsule manifests, and chunk hydration.
- Added an experimental deterministic `delta-prev-v1` predictive residual model
  with `--model auto` selection.

## 0.1.0

- Initial public reference implementation.
- Added `SLB1` exact byte artifact container.
- Added Python encode/decode/inspect CLI.
- Added specification, roadmap, and licensing policy.
