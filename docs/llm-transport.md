# LLM Transport Capsules

SPDX-License-Identifier: CC0-1.0

Star Light Codec should not ask an LLM to understand gzip, base64, or opaque
compressed bytes directly.

The transport rule is:

```text
Compressed bytes are opaque to the LLM.
The LLM receives metadata, summaries, tags, chunk indexes, and artifact refs.
Exact decode is performed by tools, not by the model.
```

This keeps the language model on semantic work and keeps byte reconstruction in
deterministic code.

## Why Not Send Gzip To The Model?

Sending compressed bytes directly to an LLM can reduce visible text length, but
it usually destroys the semantic structure the model needs:

- gzip/base64 looks like high-entropy text;
- model-side "decompression" is not a reliable exact operation;
- token savings can be offset by worse reasoning quality;
- repeated semantic context is less likely to help cache behavior;
- asking the model to output gzip/base64 pushes exact byte generation into a
  probabilistic component.

Star Light Codec uses a different split: the model sees a compact manifest, and
tools hydrate exact bytes only when needed.

## Capsule Shape

The `capsule` command writes two files:

```powershell
python -m starlight_codec capsule input.bin input.slb1 input.capsule.json `
  --summary "Fixture for codec testing" `
  --tag codec-test `
  --tag exact-roundtrip
```

The `.slb1` file is the exact byte artifact. The `.capsule.json` file is the
LLM-facing transport manifest.

Example capsule fields:

```json
{
  "schemaVersion": 1,
  "kind": "slc-llm-transport",
  "artifactRef": "input.slb1",
  "artifactContainer": "slb1",
  "artifactProfile": "starlight-byte-exact",
  "artifactDigest": "sha256:...",
  "artifactBytes": 920,
  "rawBytes": 49152,
  "inputDigest": "sha256:...",
  "classification": "text-like",
  "strategy": "gzip-recursive-base64",
  "transforms": ["gzip", "gzip"],
  "semanticTags": ["codec-test", "exact-roundtrip"],
  "summary": "Fixture for codec testing",
  "chunkIndex": [
    {
      "chunkId": "c0001",
      "start": 0,
      "end": 4096,
      "rawBytes": 4096,
      "digest": "sha256:..."
    }
  ],
  "hydrate": {
    "tool": "slc hydrate",
    "supports": ["full", "range", "chunk"],
    "rangeSyntax": "start:end"
  }
}
```

The capsule must not contain raw source bytes, base64 payloads, or embedded
artifact data.

## Hydration

Hydration restores exact bytes through the tool layer:

```powershell
python -m starlight_codec hydrate input.slb1 restored.bin
python -m starlight_codec hydrate input.slb1 range.bin --range 0:4096
python -m starlight_codec hydrate input.capsule.json chunk.bin --chunk c0001
```

The current implementation decodes the exact artifact first, then writes the
requested full output, byte range, or capsule chunk range. Future chunked
containers can make this physically selective while preserving the same
transport contract.

## Benchmark Targets

Useful comparisons:

- raw text/files in prompt;
- gzip/base64 in prompt;
- `SLB1` plus capsule only;
- capsule plus hydrated range/chunk;
- capsule plus stable delta updates.

Measure token count, correctness, latency, cache behavior, and whether the model
had enough semantic information to make the intended decision.

## Output Direction

For model output, do not ask the model to produce compressed bytes directly.
Prefer:

```text
LLM -> intent / metadata / patch / seed
tool -> artifact generation / compression / digest validation
```

The model owns meaning. The codec owns bytes.
