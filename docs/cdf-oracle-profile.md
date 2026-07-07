# CDF Oracle Profile Prototype

This document defines the first standalone CDF oracle milestone. It is a
prototype contract only; it is not integrated into the production `SLB1`
encode/decode path and does not claim `SLB1` compatibility.

## Contract

A CDF oracle profile is a deterministic function:

```text
already-decoded context bytes -> integer cumulative distribution function
```

For every byte, the encoder and decoder derive the same cumulative CDF from the
same already-decoded trailing context. The decoder must not see the original
input bytes. It receives only the entropy payload, profile metadata, and the
bytes it has already decoded.

The baseline profile is `byte-context-cdf-v0`:

- alphabet: 256 byte values
- CDF precision: `2^16`
- context window: trailing 64 decoded bytes
- toy frequency model: `1 + count(byte in trailing context)`
- integer quantization: deterministic proportional allocation to a total of
  65536, with positive frequency for every byte

The first stronger deterministic engine profile is `byte-ppm-context-v0`:

- alphabet: 256 byte values
- CDF precision: `2^16`
- context window: trailing 1024 decoded bytes
- maximum suffix order: 8 bytes
- fallback frequency model: positive base frequency plus recent decoded byte
  counts from the last 128 bytes
- PPM-style boost: find the longest prior suffix match in the decoded window
  and boost observed following bytes, with order-scaled match weight
- integer quantization: the same deterministic positive-frequency allocation
  used by the baseline profile

Both profiles keep stable human-readable spec strings, and each SHA-256 profile
hash is the registry hash of its decode-critical contract in `profiles/`.
Decode rejects metadata whose profile id, profile hash, coder id, CDF total,
context fields, profile-specific predictor fields, or final decoded input
digest do not match.

Validate the checked-in descriptor with:

```powershell
python -m starlight_codec profile validate profiles/byte-context-cdf-v0.json
python -m starlight_codec profile validate profiles/byte-ppm-context-v0.json
```

Run the standalone PPM prototype with:

```powershell
python -m starlight_codec cdf encode input.txt input.cdf input.cdf.json --profile byte-ppm-context-v0
python -m starlight_codec cdf decode input.cdf input.cdf.json output.txt
```

Run the standalone package benefit gate with:

```powershell
python -m starlight_codec cdf pack input.txt input.cdf-pack input.cdf-pack.json --profile byte-ppm-context-v0
python -m starlight_codec cdf open input.cdf-pack input.cdf-pack.json output.txt
```

## Public Resolver Interface

The standalone package lane now has a small deterministic public mirror in
`starlight_codec.cdf_public_registry`. It is local-only: no network fetch is
performed, and in the current source/editable layout the resolver loads
checked-in profile descriptors from the repository `profiles/*.json` directory
with descriptor hash validation before use or copy. `pyproject.toml` does not
yet package those descriptors as wheel data, so installed wheels should not be
treated as carrying this public mirror until a follow-up packaging pass adds
and validates package data.

Public APIs:

- `list_public_profiles()` and `list_public_components()` enumerate bundled
  profile descriptors and terminal encoder/decoder component metadata.
- `resolve_public_profile_descriptor(profile_id)` and
  `fetch_public_profile_descriptor(profile_id, output_dir)` validate and
  resolve/copy a public profile descriptor.
- `resolve_public_component(component_id)` and
  `fetch_public_component(component_id, output_dir)` validate and resolve/copy
  public component metadata.
- `plan_cdf_compression(data, profiles=...)` resolves candidate profiles and
  encoder components, then reports the package candidate plan.
- `plan_cdf_open_requirements(metadata)` inspects package metadata and returns
  the required profile and decoder components.
- `auto_pack_cdf_oracle(...)` and `auto_open_cdf_oracle_pack(...)` wrap the
  existing pack/open proof through this resolver.

CLI entry points:

```powershell
python -m starlight_codec profile list
python -m starlight_codec profile fetch byte-ppm-context-v0 .cache\slc-public
python -m starlight_codec component list
python -m starlight_codec component fetch cdf-oracle-decoder-v0 .cache\slc-public
python -m starlight_codec cdf plan input.txt --profile byte-ppm-context-v0
python -m starlight_codec cdf auto-pack input.txt input.cdf-pack input.cdf-pack.json --profile byte-ppm-context-v0 --cache-dir .cache\slc-public
python -m starlight_codec cdf auto-open input.cdf-pack input.cdf-pack.json output.txt --cache-dir .cache\slc-public
```

`cdf auto-open` does not approximate missing dependencies. It derives required
decoders and, for `cdf-oracle`, the selected public profile and entropy decoder
from package metadata. If any required public profile or component is
unavailable, it fails before opening the payload.

The package gate is still outside production `SLB1`. It compares whole-package
size for `stored`, deterministic `zlib` level 9, and selected CDF oracle
profiles, including compact sorted JSON metadata size. If no compressed
candidate beats raw input by the configured byte threshold, the package stores
the original bytes exactly and marks `recommendedForStorage` false. This is the
first safety layer that prevents random or already-compressed data from choosing
PPM merely because a PPM payload can be produced.

The standalone package path is intentionally resource-bounded while it remains
a prototype: `rawBytes` and oracle `inputBytes` are capped at 1 MiB before
expensive pack/open work, `zlib` open is bounded by declared output size, and
the selected candidate summary must match top-level package sizes. These checks
keep package metadata useful for measurement without making it a trusted oracle.

## Why This Is Not A Model Dependency

Future model weights, tokenizers, quantization settings, and local inference
backends are implementation details only if they produce the same deterministic
profile contract. A future small local LM profile would still need a fixed
profile id, a hashable profile spec, integer CDF precision, deterministic
context handling, and fail-closed decode validation.

The first milestones deliberately use no neural dependency. The toy profile is
generic byte-context adaptation, intended to prove synchronization between
encoder and decoder. The PPM profile proves the registry and profile dispatch
layer can host a stronger deterministic predictor before any model-backed
profile is considered.

## Acceptance Criteria

- CDF generation is deterministic, monotonic, positive-frequency, and totals
  exactly `2^16`.
- Encoder and decoder round-trip exact bytes using only prior decoded context.
- Empty, text/code-like, random-like, and binary-with-nulls inputs round-trip.
- Metadata/profile hash/input digest tampering fails closed.
- Predictable data has lower measured bits per byte than random-like data in
  the prototype, without claiming to beat `zlib` or production `SLB1`.
- Repeated structured fixtures show a material PPM payload-ratio improvement
  over the toy byte-frequency profile.
- The standalone package gate round-trips selected `stored`, `zlib`, and
  `cdf-oracle` payloads exactly, rejects tampered package metadata, and falls
  back to stored bytes when final whole-package size does not pass the benefit
  threshold.
- The package gate rejects oversized prototype inputs before candidate work and
  fails closed when selected candidate summaries contradict top-level package
  sizes or benefit-gate arithmetic.

## Future Promotion Gates

- A profile manifest format that hashes all decode-critical profile details.
- A registry contract such as
  [CDF Profile Registry v0](cdf-profile-registry.md) for profile identity,
  distribution, lifecycle, and negotiation.
- Cross-process and cross-platform golden CDF traces.
- Explicit versioning for profile, coder, and metadata schemas.
- Resource bounds for context length, decode time, payload size, and memory.
- Independent review of fail-closed behavior and corruption handling.
- Benchmarks against `SLB1`, stdlib compressors, and random/already-compressed
  data before any production integration.
- A benefit gate or exact fallback path for random and already-compressed data;
  the PPM oracle is not yet a production storage recommendation by itself.
