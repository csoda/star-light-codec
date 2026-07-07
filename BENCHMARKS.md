# Benchmarks

Star Light Codec benchmarks are synthetic local measurements. They are meant to
make tradeoffs visible, not to claim universal compression.

Benchmark result data in this document and under `benchmarks/results/` is
published as CC0-1.0.

## Transport Baseline

Command:

```powershell
python benchmarks\benchmark_transport.py --format json --repetitions 9 --output benchmarks\results\transport-baseline.json
python benchmarks\benchmark_transport.py --format markdown --repetitions 9
```

Environment:

- Platform: Windows-10-10.0.19045-SP0
- Python: 3.12.10
- `maxPasses`: 2
- `chunkSize`: 4096
- Timing: median of 9 measured runs after 2 warmup runs

| fixture | raw bytes | gzip bytes | gzip+b64 chars | SLB1 bytes | model SLB1 | model | capsule bytes | SLB1 saved | model saved | capsule vs raw | decision | enc ms | dec ms | cap ms | chunk ms |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| repeated_text | 92,160 | 537 | 716 | 1,077 | 1,077 | none | 4,153 | 98.83% | 98.83% | 95.49% | use-artifact-for-storage | 10.904 | 0.165 | 11.173 | 0.166 |
| json_logs | 105,984 | 2,746 | 3,664 | 2,908 | 2,908 | none | 4,580 | 97.26% | 97.26% | 95.68% | use-artifact-for-storage | 13.053 | 0.289 | 13.514 | 0.295 |
| ramp_bytes | 65,536 | 597 | 796 | 1,244 | 1,128 | delta-prev-v1 | 3,126 | 98.10% | 98.28% | 95.23% | use-artifact-for-storage | 6.342 | 0.134 | 6.444 | 0.134 |
| random_bytes | 65,536 | 65,574 | 87,432 | 66,392 | 66,392 | none | 3,114 | -1.31% | -1.31% | 95.25% | keep-original-for-storage | 7.714 | 0.131 | 8.004 | 0.133 |
| already_compressed | 65,574 | 65,614 | 87,488 | 66,430 | 66,430 | none | 3,275 | -1.31% | -1.31% | 95.01% | keep-original-for-storage | 7.826 | 0.132 | 7.976 | 0.133 |

## How To Read This

- `SLB1 bytes` is the exact artifact size. Use `SLB1 saved` for storage
  adoption.
- `model SLB1` is the `--model auto` artifact size. The model column reports
  which deterministic prediction model was selected. `none` means auto kept the
  baseline artifact.
- `capsule bytes` is the LLM-facing transport manifest size. It is not a
  substitute for storing the original bytes or artifact.
- `capsule vs raw` estimates how much smaller the model-facing manifest is than
  the raw source. This is prompt transport pressure, not compression ratio.
- `gzip+b64 chars` approximates the shape of "send gzip/base64 to the model".
  It can be compact for redundant text, but expands high-entropy inputs and is
  semantically opaque to the model.
- `decision` is the encoder's storage advice. Random and already-compressed
  inputs correctly remain `keep-original-for-storage` even though their capsule
  manifests are small.

## Takeaways

- Redundant text/log fixtures compress well as `SLB1`, with exact decode under
  1 ms in this local run.
- The experimental `delta-prev-v1` model only won on `ramp_bytes`, reducing
  `SLB1` from 1,244 bytes to 1,128 bytes in this run. It stayed off for text,
  logs, random bytes, and already-compressed input.
- High-entropy inputs are detected as storage non-winners. The artifact remains
  exact, but callers should keep the original for storage.
- Capsule manifests stay small because they carry metadata, digests, tags, and a
  chunk index instead of raw bytes.
- The current `hydrate --chunk` path decodes the full artifact before slicing.
  Future physical chunked containers should reduce this cost for large files
  while preserving the same capsule contract.

Machine-readable baseline: [benchmarks/results/transport-baseline.json](benchmarks/results/transport-baseline.json).
