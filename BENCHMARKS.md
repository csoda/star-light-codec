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

| fixture | raw bytes | gzip bytes | gzip+b64 chars | SLB1 bytes | capsule bytes | SLB1 saved | capsule vs raw | decision | enc ms | dec ms | cap ms | chunk ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| repeated_text | 92,160 | 537 | 716 | 1,040 | 4,093 | 98.87% | 95.56% | use-artifact-for-storage | 10.844 | 0.164 | 11.466 | 0.163 |
| json_logs | 105,984 | 2,746 | 3,664 | 2,871 | 4,520 | 97.29% | 95.74% | use-artifact-for-storage | 13.286 | 0.290 | 13.683 | 0.321 |
| random_bytes | 65,536 | 65,574 | 87,432 | 66,355 | 3,054 | -1.25% | 95.34% | keep-original-for-storage | 7.959 | 0.132 | 10.508 | 0.133 |
| already_compressed | 65,574 | 65,614 | 87,488 | 66,393 | 3,215 | -1.25% | 95.10% | keep-original-for-storage | 7.936 | 0.130 | 8.169 | 0.129 |

## How To Read This

- `SLB1 bytes` is the exact artifact size. Use `SLB1 saved` for storage
  adoption.
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
- High-entropy inputs are detected as storage non-winners. The artifact remains
  exact, but callers should keep the original for storage.
- Capsule manifests stay small because they carry metadata, digests, tags, and a
  chunk index instead of raw bytes.
- The current `hydrate --chunk` path decodes the full artifact before slicing.
  Future physical chunked containers should reduce this cost for large files
  while preserving the same capsule contract.

Machine-readable baseline: [benchmarks/results/transport-baseline.json](benchmarks/results/transport-baseline.json).
