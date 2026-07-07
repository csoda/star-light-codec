# CDF Profile Registry v0

SPDX-License-Identifier: CC0-1.0

Status: experimental planning specification.

This document defines `slc-cdf-profile-registry-v0`, a registry and manifest
shape for deterministic CDF oracle profiles used by future Star Light Codec
entropy-coded artifacts.

The registry is not a single universal profile. It is a way to name, hash,
distribute, negotiate, and preserve many profiles while keeping decode exact.

## Source Model

This specification follows the same general pattern used by existing standards:

- MIME `codecs` and `profiles` parameters let receivers decide whether content
  is supported before rendering.
- Zstandard dictionary IDs identify an external decode dependency, with public
  and private allocation paths.
- HTTP compression dictionary transport separates dictionary discovery, hash,
  opaque server ID, cache freshness, and matching scope.
- AV1 profiles and levels separate feature support from resource and
  performance requirements.
- Neural compression research shows that predictive models can be turned into
  lossless compressors when paired with arithmetic coding or ANS, but practical
  systems must account for runtime cost and determinism.

References are listed at the end of this document.

## Goals

- Allow multiple purpose-specific CDF profiles.
- Let an artifact declare exactly which profile is needed to decode it.
- Make a profile identity hashable and reproducible.
- Keep model-backed decode deterministic, fail-closed, and testable.
- Support public, private, and bundled profile distribution modes.
- Let communication endpoints negotiate supported profiles without requiring a
  single global codec choice.
- Preserve exact fallback behavior when a profile is unavailable or untrusted.

## Non-Goals

- This is not encryption. A public profile is shared knowledge, not a secret.
- This does not define a production `SLB1` integration yet.
- This does not require one global Star Light Codec profile.
- This does not permit probabilistic text generation as decode.
- This does not allow remote model calls during required exact decode.
- This does not make model weights free. Profile weight size, runtime, license,
  and lifetime remain part of the operational cost.

## Layering

Future CDF-backed artifacts should use two layers:

```text
container/envelope  -> artifact bytes, payload, digests, profile reference
cdf profile         -> deterministic CDF oracle and entropy coder contract
```

The envelope identifies the profile. The profile defines how the CDF is
computed and how the entropy payload is decoded.

Example names:

```text
container: slc-cdf-container-v1
profile:   org.starlight.codec.cdf.stream-small-v1
profile:   org.starlight.codec.cdf.code-md-public-v1
profile:   org.starlight.codec.cdf.archive-large-v1
profile:   private.example.team.logs-sha256-3f4a...
```

## Profile Classes

| Class | Purpose | Expected tradeoff |
| --- | --- | --- |
| `stream-small` | Low-latency communication | Small model, bounded context, fast fallback |
| `code-md-public` | Code, Markdown, JSON, docs | Better text prediction, moderate local cost |
| `archive-large` | Offline compression and archives | Heavy decode allowed, strong lifetime rules |
| `private-domain` | Project or organization data | Highest local fit, weakest public portability |
| `bundled` | Self-contained archival artifact | Larger artifact, strongest future decode |

The class is advisory. Exact compatibility is determined by the profile hash
and decode contract, not the class label.

## Canonical Descriptor

Each profile is described by a JSON-compatible descriptor. The profile hash is
computed over the `decodeContract` object encoded as `slc-canonical-json-v0`.
The descriptor digest is computed over the full descriptor encoded the same way
after excluding the `descriptorDigest` field itself.

`slc-canonical-json-v0` rules:

- UTF-8 bytes.
- Object keys sorted by Unicode code point.
- No insignificant whitespace.
- Strings use JSON escaping only where required by JSON.
- Arrays remain in declared order.
- Decode-critical numbers are non-negative integers only.
- Floating-point values are forbidden in `decodeContract`.
- `profileHash` covers only `decodeContract`.
- `descriptorDigest` covers the full descriptor excluding `descriptorDigest`.

The profile hash format is:

```text
sha256:<64 lowercase hex>
```

## Descriptor Schema

This is the required v0 descriptor shape. Fields marked optional may be absent,
but if present they are still covered by the descriptor file hash. Fields under
`decodeContract` are decode-critical and are covered by `profileHash`.

```json
{
  "schema": "slc-cdf-profile-registry-v0",
  "profileId": "org.starlight.codec.cdf.code-md-public-v1",
  "profileVersion": 1,
  "profileClass": "code-md-public",
  "status": "experimental",
  "profileHash": "sha256:<decodeContract hash>",
  "descriptorDigest": "sha256:<full descriptor hash>",
  "availability": "public-registry",
  "decodeContract": {
    "oracleKind": "model-logits",
    "symbolAlphabet": "byte256",
    "context": {
      "unit": "byte",
      "maxContextBytes": 65536,
      "slicing": "left-truncate"
    },
    "model": {
      "format": "gguf",
      "architecture": "example-transformer",
      "weightsDigest": "sha256:<weights hash>",
      "configDigest": "sha256:<config hash>",
      "tokenizerDigest": "sha256:<tokenizer hash>",
      "quantization": "q8_0"
    },
    "runtime": {
      "backendId": "slc-reference-cpu-v0",
      "backendDigest": "sha256:<backend contract hash>",
      "deviceClass": "cpu",
      "batchSize": 1,
      "threads": 1
    },
    "logitsToCdf": {
      "logitQuantization": "int32-fixed-scale-v0",
      "temperatureNumerator": 1,
      "temperatureDenominator": 1,
      "cdfTotal": 65536,
      "minimumSymbolFrequency": 1,
      "rounding": "largest-remainder-stable-index"
    },
    "entropyCoder": {
      "coderId": "slc-range-coder-v0",
      "stateBits": 64,
      "flushRule": "minimal-final-interval-v0"
    },
    "resourceLimits": {
      "maxDecodeMemoryBytes": 1073741824,
      "maxProfileBytes": 8589934592,
      "maxPayloadExpansionRatio": 1024,
      "streaming": false
    },
    "goldenVectors": [
      {
        "name": "empty",
        "inputDigest": "sha256:<raw input hash>",
        "cdfTraceDigest": "sha256:<canonical CDF trace hash>",
        "payloadDigest": "sha256:<entropy payload hash>",
        "decodedDigest": "sha256:<decoded input hash>"
      }
    ]
  },
  "distribution": {
    "primaryUri": "https://example.invalid/slc/profiles/code-md-public-v1.json",
    "assetUris": [],
    "license": "unspecified",
    "publishedAt": "2026-07-08",
    "retainUntil": "2031-07-08"
  },
  "compatibility": {
    "containers": ["slc-cdf-container-v1"],
    "supersedes": [],
    "notCompatibleWith": []
  },
  "security": {
    "secretProfile": false,
    "attackerControlledInputWarning": true,
    "notes": "Compression is not encryption."
  }
}
```

## Required Fields

| Field | Requirement |
| --- | --- |
| `schema` | Must be `slc-cdf-profile-registry-v0`. |
| `profileId` | Stable identifier. Reverse-DNS style is recommended for public profiles. |
| `profileVersion` | Integer version within a profile family. |
| `profileClass` | Advisory use class. |
| `status` | One of `experimental`, `stable`, `deprecated`, `discouraged`, or `revoked`. |
| `profileHash` | SHA-256 of canonical `decodeContract`. |
| `descriptorDigest` | SHA-256 of the full canonical descriptor excluding `descriptorDigest`. |
| `availability` | One of `public-registry`, `private-arrangement`, `bundled`, or `inline-descriptor`. |
| `decodeContract` | Complete deterministic decode contract. |

## Decode-Critical Contract

The following fields are decode-critical. A decoder must reject the profile if
any required decode-critical field is missing, malformed, unsupported, or does
not match the artifact's profile reference.

- symbol alphabet and byte/token mapping;
- tokenizer, normalization, and pre-tokenization, when tokens are used;
- model format, architecture, weights hash, config hash, and quantization;
- runtime backend contract, device class, batch size, context slicing, and
  thread/concurrency rule;
- logits extraction and logits-to-CDF quantization;
- CDF total, minimum symbol frequency, and rounding rule;
- arithmetic/rANS/range coder parameters;
- resource limits;
- golden vector digests.

No decode-critical rule may depend on wall-clock time, network access, random
sampling, GPU autotuning, unspecified floating-point behavior, or implementation
defaults outside the profile.

## Runnable Prototype Descriptors

This repository includes runnable descriptors for the standalone toy oracle and
the first stronger deterministic PPM-style oracle:

```powershell
python -m starlight_codec profile validate profiles/byte-context-cdf-v0.json
python -m starlight_codec profile show profiles/byte-context-cdf-v0.json
python -m starlight_codec profile validate profiles/byte-ppm-context-v0.json
python -m starlight_codec profile show profiles/byte-ppm-context-v0.json
```

The first validator milestone supports the `byte-context-cdf-v0` decode
contract and the `byte-ppm-context-v0` decode contract. It rejects unknown
decode-critical rules, floats, unsupported statuses, mismatched oracle/model
pairs, and digest mismatches before profile use.

`byte-ppm-context-v0` is still a deterministic byte-context profile, not a
neural model or LLM-backed decoder. It uses a bounded prior decoded window,
recent byte counts, and longest-suffix follow-byte boosts to prove the registry
can host a stronger predictor without changing the arithmetic coder.

The standalone CDF package gate is a separate envelope experiment, not a
registry promotion. `python -m starlight_codec cdf pack` chooses among stored
bytes, deterministic `zlib` level 9, and selected CDF oracle profiles by
estimated whole-package size. Its metadata embeds the selected oracle metadata
only when `selectedCodec` is `cdf-oracle`, and `cdf open` validates that
metadata with the existing oracle decoder before returning bytes.

## Artifact Binding

A future CDF-backed artifact must bind to a profile in its envelope. The
minimum binding fields are:

```json
{
  "cdf": {
    "container": "slc-cdf-container-v1",
    "profileId": "org.starlight.codec.cdf.code-md-public-v1",
    "profileHash": "sha256:<decodeContract hash>",
    "descriptorDigest": "sha256:<full descriptor hash>",
    "profileResolution": "public-registry",
    "coderId": "slc-range-coder-v0",
    "payloadDigest": "sha256:<entropy payload hash>",
    "fallbackMode": "none"
  }
}
```

The decoder must:

1. Parse the artifact envelope.
2. Resolve the profile by `profileId` and `profileHash`.
3. Verify the descriptor hash and decode contract hash.
4. Verify that the profile status is allowed by local policy.
5. Verify local runtime support for every decode-critical rule.
6. Optionally run cached golden vector checks when installing or first using the
   profile.
7. Verify the entropy payload digest.
8. Decode with the specified profile and coder.
9. Verify final length and input digest from the envelope.

Artifacts must not silently substitute a newer profile. A superseding profile
may be used only for new encodes, never to decode an artifact that names an
older profile.

## Resolution Modes

| Mode | Meaning | Decode behavior |
| --- | --- | --- |
| `public-registry` | Profile descriptor and assets are public by hash. | Resolve locally or from configured mirrors, then verify hashes. |
| `private-arrangement` | Parties know the profile out of band. | Decode only when local policy has an exact matching profile. |
| `bundled` | Artifact includes all required profile assets. | Verify bundled assets before decode; artifact size includes profile cost. |
| `inline-descriptor` | Artifact includes descriptor but not heavy assets. | Verify descriptor, then resolve assets by hash. |

If a required profile is unavailable, the exact decoder must fail with a missing
profile error unless the artifact contains a separate exact fallback payload.

## Fallback Policy

Fallback is an encoder and envelope decision, not a permission for approximate
decode.

Valid v0 fallback modes:

| Mode | Meaning |
| --- | --- |
| `none` | Artifact requires the named profile. |
| `stored-alternative` | Artifact also stores an exact non-CDF payload. |
| `stdlib-alternative` | Artifact also stores an exact baseline transform payload. |
| `residual-contained` | Artifact stores residual data sufficient for exact decode with the named profile. |

When a fallback is present, the envelope must include independent digest and
length metadata for each fallback payload. A decoder may choose fallback only if
the chosen path verifies the final input digest.

## Registry Index

A registry index is a JSON object that maps profile IDs to descriptor digests
and locations:

```json
{
  "schema": "slc-cdf-profile-index-v0",
  "registryId": "org.starlight.codec.public",
  "updatedAt": "2026-07-08",
  "profiles": [
    {
      "profileId": "org.starlight.codec.cdf.code-md-public-v1",
      "profileHash": "sha256:<decodeContract hash>",
      "descriptorDigest": "sha256:<full descriptor hash>",
      "status": "experimental",
      "class": "code-md-public",
      "descriptorUri": "https://example.invalid/slc/profiles/code-md-public-v1.json",
      "recommended": false
    }
  ]
}
```

Registry index ordering is not meaningful. Decoders must use hash identity, not
array position.

## Negotiation

Communication protocols may advertise supported profile hashes before sending a
CDF-backed artifact.

The minimum negotiation tuple is:

```text
profileId; profileHash; class; maxPayloadBytes; maxDecodeMemoryBytes; q
```

`q` is a local preference weight, similar in spirit to HTTP content negotiation.
It must not override exact compatibility. If there is no shared acceptable
profile, the sender should choose a baseline exact format or send no compressed
variant.

Negotiation is advisory. The artifact envelope remains authoritative.

## Lifecycle

Profile status values:

| Status | Meaning |
| --- | --- |
| `experimental` | May change by publishing a new profile ID/version. Not recommended for long-term archives. |
| `stable` | Expected to remain decode-supported for the declared retention period. |
| `deprecated` | Valid for old decode, not recommended for new encodes. |
| `discouraged` | Valid only by explicit local policy because of cost, security, or quality concerns. |
| `revoked` | Must not be used unless local policy explicitly allows forensic decode. |

Changing any decode-critical behavior requires a new `profileHash`. Public
profiles should also use a new `profileId` or `profileVersion` when human
meaning changes.

## Resource Policy

Every profile must declare resource limits. Decoders may enforce stricter local
limits and reject artifacts that exceed them.

Required limits:

- maximum profile asset bytes;
- maximum decode memory;
- maximum payload expansion ratio;
- streaming support flag;
- context window size;
- maximum symbol alphabet size;
- maximum decoder state bits.

Resource limits are part of compatibility, not mere documentation.

## Security Notes

- Compression is not encryption.
- Secret or private profiles must be treated as sensitive model/dictionary
  material, not as artifact payload secrecy.
- Compression over attacker-controlled and secret-adjacent data can create side
  channels. Communication profiles should define origin, scope, and cache rules
  when shared dictionaries or private profiles are involved.
- Profile descriptors must not contain executable code.
- Decoders should prefer data-only model formats and fixed local runtimes.
- Hash checks must happen before profile use.
- Revoked or discouraged profiles require explicit local policy.

## Acceptance Criteria

A profile implementation can be considered compatible with this v0 registry
only when all of the following pass:

- Descriptor canonicalization produces the same `profileHash` on at least two
  supported platforms.
- Golden vector decode passes from a clean process with no network access.
- Metadata tampering of `profileId`, `profileHash`, `descriptorDigest`,
  `coderId`, payload digest, or fallback metadata fails closed.
- Unsupported status or resource limits fail before entropy decode begins.
- Random, already-compressed, text/code-like, and empty fixtures either decode
  exactly or select an exact fallback path.
- Artifact inspection can report a missing profile without attempting decode.
- Public documentation states whether profile asset cost is included in any
  compression-ratio claim.
- Random or already-compressed data has a benefit gate or exact fallback before
  any CDF profile is promoted into production artifact selection.

## Open Questions

- Whether the first model-backed profile should use bytes directly or a
  tokenizer-backed symbol stream.
- Whether the reference deterministic backend should be a tiny integer-only CPU
  implementation or a constrained GGUF/ONNX runtime profile.
- Whether public registry descriptors should be signed in v1, or whether
  content-addressed hashes are enough for the experimental phase.
- How much profile lifetime guarantee is realistic for public mirrors.

## References

- [RFC 6381: Codecs and Profiles Parameters](https://datatracker.ietf.org/doc/html/rfc6381)
- [RFC 8878: Zstandard Compression](https://datatracker.ietf.org/doc/rfc8878/)
- [RFC 9842: Compression Dictionary Transport](https://datatracker.ietf.org/doc/rfc9842/)
- [RFC 9110: HTTP Semantics](https://datatracker.ietf.org/doc/html/rfc9110)
- [AV1 Bitstream and Decoding Process Specification](https://aomediacodec.github.io/av1-spec/av1-spec.pdf)
- [ONNX Intermediate Representation Specification](https://onnx.ai/onnx/repo-docs/IR.html)
- [GGUF Specification](https://github.com/ggml-org/ggml/blob/master/docs/gguf.md)
- [PyTorch Reproducibility Notes](https://docs.pytorch.org/docs/2.12/notes/randomness.html)
- [LLMZip](https://arxiv.org/abs/2306.04050)
- [Language Modeling Is Compression](https://arxiv.org/abs/2309.10668)
- [FineZip](https://arxiv.org/html/2409.17141v1)
