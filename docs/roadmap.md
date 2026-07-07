# Star Light Codec Roadmap

Star Light Codec starts with a small exact byte container because that gives the
project a safe base: every experiment must either round-trip exactly or state
clearly that it is a different lossy/perceptual track.

## R1. Public Exact Codec Baseline

Status: active.

- Publish the `SLB1` container.
- Keep the Python reference implementation short and readable.
- Keep exact round-trip and digest validation as the default.
- Report whole-artifact size, not only payload size.
- Avoid adopting compressed artifacts when the whole artifact is larger than the
  source.

## R2. LLM Transport Capsules

Status: active.

- Keep compressed bytes opaque to LLMs.
- Use transport capsules for artifact refs, semantic tags, stable summaries, and
  chunk indexes.
- Hydrate exact bytes through tools instead of asking models to decompress or
  generate compressed payloads.
- Add token/quality/cache benchmarks for raw prompt vs gzip/base64 prompt vs
  capsule-only vs capsule-plus-hydration workflows.

## R3. Stronger Encoder Planning

Status: planned.

- Add entropy estimation before compression.
- Detect text, JSON, logs, sparse bytes, and repeated binary regions.
- Select transforms by data shape.
- Add a no-benefit fast path for random or already-compressed data.
- Keep decoder-compatible transforms stable where possible.

## R4. Chunking And Dictionaries

Status: planned.

- Add chunked artifacts for mixed data.
- Explore content-defined chunking.
- Add small local dictionaries for repeated JSON keys, logs, and project memory.
- Add Merkle-style chunk digests for partial validation and dedupe.

## R5. Domain-Specific Codecs

Status: planned.

- Audio residual payloads from SLAC experiments.
- Log and trace summaries.
- Screenshot or browser-artifact metadata.
- Game-development artifacts where human judgment can choose useful lossy
  representations.

## R6. Authenticated Sealing

Status: planned.

This track is separate from compression. A sealed artifact should authenticate
metadata before decrypting, keep key material outside the artifact, and use
well-reviewed cryptographic primitives.

The Star Light prototype already has a local experimental seal/open lane. This
repository will only promote a seal format after a threat model and focused
security review.

## R7. Public Benchmarks

Status: planned.

- Keep synthetic fixtures separate from maintainer-local observations.
- Report raw bytes, payload bytes, whole artifact bytes, encode/decode time, and
  whether storage adoption is recommended.
- Avoid claiming universal compression from small or hand-picked fixtures.

## R8. Language Ports

Status: planned.

- Keep Python as the reference implementation.
- Add JavaScript/TypeScript for browser and tool integration.
- Add PowerShell compatibility tests against Star Light.
- Add Rust or Go only if the format stabilizes enough to justify it.
