# Star Light Codec

Experimental exact byte compression from the Star Light project.

Star Light Codec is a small reference project for the byte-artifact codec first
tested inside [codex-starlight](https://github.com/csoda/codex-starlight). It is
not a media codec pack, not a video codec, and not related to Astro Starlight.

The current public slice is deliberately narrow:

- exact round-trip for arbitrary byte files;
- a compact `SLB1` binary artifact container;
- bounded gzip transform planning;
- SHA-256 validation for the payload and original input;
- storage-adoption metadata so incompressible data can stay uncompressed;
- a compatibility profile for Star Light's `starlight-byte-exact` artifacts.

This repository starts with a readable Python reference implementation. Future
work can add stronger encoders, domain-specific codecs, and authenticated sealed
artifacts without making the initial format harder to audit.

## Quick Start

```powershell
python -m pip install -e .[test]
python -m starlight_codec encode README.md README.slb1 --max-passes 2
python -m starlight_codec inspect README.slb1
python -m starlight_codec decode README.slb1 README.roundtrip.md
pytest
```

The encoder writes an artifact. The decoder reconstructs the exact original
bytes. The command output is metadata only; it does not print the package
payload.

## What This Is

Star Light Codec treats a compressed artifact as a small self-describing
capsule. The decoder stays simple: it reads the container, checks sizes and
digests, then applies allowlisted transforms in reverse order. Encoder planning
can improve over time as long as it keeps using compatible primitive
transforms.

The current `SLB1` artifact is:

```text
magic          4 bytes   ASCII "SLB1"
headerLength   4 bytes   little-endian uint32
payloadLength  8 bytes   little-endian uint64
header         N bytes   UTF-8 compact JSON
payload        M bytes   raw transformed payload bytes
```

See [docs/spec.md](docs/spec.md) for the exact format contract.

## What This Is Not

- Not a replacement for gzip, zstd, Brotli, PNG, MP3, Opus, or other mature
  codecs.
- Not a claim of universal compression.
- Not a machine-learning compressor.
- Not a production security system.
- Not a codec pack for playing media files.

The current encoder mostly demonstrates the container contract and exact
validation flow. On redundant data it can be small; on random data it should
report `keep-original-for-storage`.

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
are smarter encoder planning, chunking, dictionaries, domain-specific residual
codecs, and a separate authenticated sealing layer.

## Name Check

The project name is **Star Light Codec**. A quick public search found nearby
names such as `StarCodec`, `Stable Codec`, and many `Starlight` documentation or
camera-related projects, but no obvious exact public project named
`Star Light Codec`.
This is not legal advice. The README and package description intentionally avoid
claiming media-codec-pack behavior.

## Licensing

This repository follows the same policy as Star Light:

- Code, scripts, and tests: Apache-2.0.
- Documentation and specifications: CC BY 4.0.
- Small examples, fixtures, and metadata intended for reuse: CC0-1.0.

See [LICENSING.md](LICENSING.md).
