# Star Light Codec

Experimental exact byte artifact codec from the Star Light project.

Star Light Codec packages arbitrary byte files into self-describing artifacts
that can be decoded back to the exact original bytes. The first public format,
`SLB1`, is small on purpose: it combines a binary payload, compact JSON metadata,
bounded transform planning, and SHA-256 validation into one auditable container.

This is not a media codec pack, not a video codec, and not related to Astro
Starlight.

The current public slice is deliberately narrow:

- exact round-trip for arbitrary byte files;
- a compact `SLB1` binary artifact container;
- bounded gzip transform planning;
- an optional experimental predictive residual model layer;
- SHA-256 validation for the payload and original input;
- storage-adoption metadata so incompressible data can stay uncompressed;
- a compatibility profile for Star Light's `starlight-byte-exact` artifacts;
- an encoder/decoder split where encoders can improve while decoders stay
  simple.

The important part is not that the first encoder uses gzip. The important part
is the artifact contract: a decoder can restore exact bytes without knowing the
source file type, the payload can be validated before and after transforms, and
new encoder planners can compete on compression ratio without changing the
baseline decode model.

This repository starts with a readable Python reference implementation. Future
work can add stronger encoders, chunking, dictionaries, domain-specific codecs,
and authenticated sealed artifacts without making the initial format harder to
audit.

## Quick Start

```powershell
python -m pip install -e .[test]
python -m starlight_codec encode README.md README.slb1 --max-passes 2
python -m starlight_codec inspect README.slb1
python -m starlight_codec decode README.slb1 README.roundtrip.md
python -m starlight_codec capsule README.md README.slb1 README.capsule.json --tag docs
python -m starlight_codec hydrate README.capsule.json README.chunk.md --chunk c0001
pytest
```

The encoder writes an artifact. The decoder reconstructs the exact original
bytes. The command output is metadata only; it does not print the package
payload.

## Why This Is Strong

- **Arbitrary bytes:** text, JSON, logs, binaries, generated artifacts, and
  unknown file types all use the same exact-byte interface.
- **Exactness is checked, not assumed:** `SLB1` stores the original byte length,
  transformed payload length, payload digest, and final input digest.
- **The decoder is intentionally boring:** it reads the header, verifies the
  artifact, applies allowlisted transforms in reverse order, and verifies the
  reconstructed bytes.
- **Encoder evolution is separated from decode safety:** better planners can
  choose chunks, dictionaries, residuals, or future domain-specific strategies
  while preserving an exact compatibility contract.
- **Compression adoption is honest:** metadata reports whether the whole
  artifact is smaller than the source. If not, callers can keep the original.

## Technical Shape

`SLB1` is a self-contained exact-byte artifact:

The current `SLB1` artifact is:

```text
magic          4 bytes   ASCII "SLB1"
headerLength   4 bytes   little-endian uint32
payloadLength  8 bytes   little-endian uint64
header         N bytes   UTF-8 compact JSON
payload        M bytes   raw transformed payload bytes
```

The header records the compatibility profile:

- `schemaVersion: 2`
- `packageKind: starlight-byte-exact`
- `artifactContainer: slb1`
- `packageFormat: layered`
- `strategy: stored-base64 | gzip-base64 | gzip-recursive-base64 | delta-prev-*`
- `transforms: [] | ["gzip"] | ["delta-prev-v1", "gzip", ...]`
- `inputDigest` and `payloadDigest` as `sha256:<64 hex>`

The payload is not embedded in JSON. It is stored as raw bytes after the header,
so the container avoids base64 expansion while keeping the metadata inspectable.

See [docs/spec.md](docs/spec.md) for the exact format contract.

## What This Is Not

- Not a replacement for gzip, zstd, Brotli, PNG, MP3, Opus, or other mature
  codecs.
- Not a claim of universal compression.
- Not a neural machine-learning compressor.
- Not a production security system.
- Not a codec pack for playing media files.

The current encoder mostly demonstrates the container contract and exact
validation flow. On redundant data it can be small; on random or already
compressed data it should report `keep-original-for-storage`.

## Current Encoder

The reference encoder uses a bounded transform planner:

1. classify the input shape;
2. try up to four gzip passes;
3. stop when a pass does not reduce payload size;
4. write `stored-base64`, `gzip-base64`, or `gzip-recursive-base64` strategy
   metadata;
5. compare whole artifact size against the source;
6. report `use-artifact-for-storage` only when the full artifact is smaller.

This is a baseline, not the ceiling. The roadmap is to make the encoder smarter
while keeping exact round-trip and fail-closed decode behavior as the invariant.

## Experimental Model Layer

Star Light Codec can also try a small deterministic prediction model before
compression:

```powershell
python -m starlight_codec encode input.bin input.slb1 --model auto
python -m starlight_codec capsule input.bin input.slb1 input.capsule.json --model auto
```

The first model is `delta-prev-v1`. It predicts each byte from the previous
byte, stores the byte-wise residual, then lets the normal bounded gzip planner
compress that residual. This is not a neural compressor and it is not lossy:
the model id, model hash, transform stack, payload digest, and final input
digest are all stored so decode remains exact and fail-closed.

`--model auto` compares the baseline encoder with the modeled encoder and keeps
the modeled artifact only when the whole `SLB1` artifact is smaller. The default
is still `--model none` for maximum compatibility with the baseline `SLB1`
contract.

## LLM Transport Capsules

Do not ask an LLM to understand gzip, base64, or compressed payload bytes
directly. Treat compressed bytes as opaque.

Star Light Codec now includes an LLM-facing transport layer:

```powershell
python -m starlight_codec capsule input.bin input.slb1 input.capsule.json `
  --summary "Asset metadata fixture" `
  --tag exact-roundtrip

python -m starlight_codec hydrate input.capsule.json chunk.bin --chunk c0001
python -m starlight_codec hydrate input.slb1 range.bin --range 0:4096
```

The capsule is a compact JSON manifest for the model: artifact reference,
digests, sizes, strategy, semantic tags, summary, and chunk index. It does not
embed raw bytes or base64 payloads. Hydration is performed by the tool layer so
the model can reason over metadata and request exact bytes only when needed.

See [docs/llm-transport.md](docs/llm-transport.md).

## Example Metadata

```json
{
  "schemaVersion": 2,
  "codec": "starlight-byte-exact",
  "container": "slb1",
  "strategy": "gzip-recursive-base64",
  "rawBytes": 49152,
  "payloadBytes": 113,
  "artifactBytes": 920,
  "recommendedForStorage": true,
  "adoptionDecision": "use-artifact-for-storage"
}
```

## Roadmap

The roadmap is in [docs/roadmap.md](docs/roadmap.md). The next planned tracks
are smarter encoder planning, physical chunked containers, dictionaries,
domain-specific residual codecs, and a separate authenticated sealing layer.

## Benchmarks

Synthetic local benchmark results are in [BENCHMARKS.md](BENCHMARKS.md).
The current baseline compares raw bytes, gzip, gzip+base64, `SLB1`,
`--model auto`, and LLM-facing capsule manifests across redundant text, JSON
logs, ramp bytes, random bytes, and already-compressed input.

## Name Check

The project name is **Star Light Codec**. A quick public search found nearby
names such as `StarCodec`, `Stable Codec`, and many `Starlight` documentation or
camera-related projects, but no obvious exact public project named
`Star Light Codec`.
This is not legal advice. The README and package description intentionally avoid
claiming media-codec-pack behavior.

## Licensing

This repository follows the same policy as Star Light:

- Reference implementation code, CLI, tests, and benchmark scripts: Apache-2.0.
- Codec format, compatibility profile, schemas, transport capsule spec, test
  vectors, fixtures, sample metadata, and benchmark result data: CC0-1.0.
- Narrative docs, README files, and roadmap text: CC BY 4.0 unless marked
  otherwise.

See [LICENSING.md](LICENSING.md).
