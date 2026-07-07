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

| fixture | raw bytes | gzip bytes | gzip+b64 chars | SLB1 bytes | model SLB1 | strong SLB1 | planner | model | capsule bytes | SLB1 saved | strong saved | capsule vs raw | decision | enc ms | dec ms | cap ms | chunk ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| repeated_text | 92,160 | 537 | 716 | 1,094 | 1,094 | 1,094 | gzip | none | 4,195 | 98.81% | 98.81% | 95.45% | use-artifact-for-storage | 11.238 | 0.175 | 11.290 | 0.173 |
| json_logs | 105,984 | 2,746 | 3,664 | 2,925 | 2,925 | 2,499 | stdlib-auto | none | 4,622 | 97.24% | 97.64% | 95.64% | use-artifact-for-storage | 13.304 | 0.308 | 13.642 | 0.321 |
| ramp_bytes | 65,536 | 597 | 796 | 1,261 | 1,145 | 1,113 | stdlib-auto | delta-prev-v1 | 3,168 | 98.08% | 98.30% | 95.17% | use-artifact-for-storage | 6.459 | 0.144 | 6.488 | 0.146 |
| random_bytes | 65,536 | 65,574 | 87,432 | 66,409 | 66,409 | 66,409 | gzip | none | 3,156 | -1.33% | -1.33% | 95.18% | keep-original-for-storage | 7.758 | 0.136 | 8.013 | 0.137 |
| already_compressed | 65,574 | 65,614 | 87,488 | 66,447 | 66,447 | 66,447 | gzip | none | 3,317 | -1.33% | -1.33% | 94.94% | keep-original-for-storage | 7.886 | 0.137 | 8.054 | 0.138 |

## How To Read This

- `SLB1 bytes` is the exact artifact size. Use `SLB1 saved` for storage
  adoption.
- `model SLB1` is the `--model auto` artifact size. The model column reports
  which deterministic prediction model was selected. `none` means auto kept the
  baseline artifact.
- `strong SLB1` is the current strongest reference path:
  `--planner stdlib-auto --model auto`. It compares whole artifacts, so it can
  fall back to `gzip` when the stronger planner would only add metadata
  overhead.
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
- `stdlib-auto` improved `json_logs` from 2,925 to 2,499 bytes, while
  `delta-prev-v1` plus `stdlib-auto` improved `ramp_bytes` from 1,261 to 1,113
  bytes in this run. It fell back to gzip for fixtures where metadata overhead
  would erase the win.
- High-entropy inputs are detected as storage non-winners. The artifact remains
  exact, but callers should keep the original for storage.
- Capsule manifests stay small because they carry metadata, digests, tags, and a
  chunk index instead of raw bytes.
- The current `hydrate --chunk` path decodes the full artifact before slicing.
  Future physical chunked containers should reduce this cost for large files
  while preserving the same capsule contract.

Machine-readable baseline: [benchmarks/results/transport-baseline.json](benchmarks/results/transport-baseline.json).

## Real-Data Benchmark Harness

Use the real-data harness when you want to compare Star Light Codec against
standard compressors on local files without publishing file contents:

```powershell
python benchmarks\benchmark_real_data.py README.md src tests `
  --label-root . `
  --format markdown `
  --max-file-bytes 1048576
```

For a machine-readable local report:

```powershell
python benchmarks\benchmark_real_data.py path\to\data `
  --label-root path\to\data `
  --format json `
  --output benchmarks\results\real-data-local.json
```

The harness reports:

- raw file size;
- best standard compressor available locally: `gzip`, `zlib`, `bz2`, `lzma`,
  plus optional `brotli` or `zstd` if installed;
- baseline `SLB1` size;
- `--model auto` `SLB1` size and selected prediction model;
- strong `SLB1` size for `--planner stdlib-auto --model auto`;
- exact decode verification status.

Privacy and publication notes:

- Raw file contents are never embedded in the report.
- Absolute paths are avoided when `--label-root` is provided.
- SHA-256 digests are omitted by default; use `--include-digest` only when the
  benchmark output is safe to share.
- Files above `--max-file-bytes` are skipped by default to keep runs bounded.
- Generated/cache artifacts such as `__pycache__`, `*.pyc`, `*.pyo`, and
  `*.egg-info` are excluded by default; use `--no-default-excludes` to include
  them.

This real-data harness is the next step before making broad compression-ratio
claims. Synthetic fixtures show behavior shape; real data shows whether a model
is useful outside a toy pattern.

## Predictor Search Harness

Use the predictor search harness to let a small controller try deterministic
prediction candidates under a strict budget:

```powershell
python benchmarks\search_predictors.py README.md src tests `
  --label-root . `
  --search-mode adaptive `
  --time-limit-seconds 30 `
  --candidate-limit 64 `
  --file-limit 64
```

The harness is intentionally separate from the production codec. It creates
temporary experimental `SLP1` artifacts, verifies exact round-trip for every
candidate, and reports whether a candidate should be promoted, watched, or
rejected. It does not embed raw file contents in the report.

Search modes:

- `adaptive`: default. A tiny in-run learning controller scores candidates from
  sample residual hints, learned transform/compressor rewards, exploration, and
  speed penalty.
- `exhaustive`: deterministic candidate order for control runs and debugging.

Safety limits:

- `--time-limit-seconds` stops exploration after the requested wall-clock
  budget.
- `--candidate-limit` bounds candidate count.
- `--file-limit` bounds corpus size.
- `--max-file-bytes` skips large files.

Current candidate families are deliberately simple and lossless:

- `identity+compressor` controls;
- `delta-prev-N+compressor`;
- `xor-prev-N+compressor`;
- `delta-avg2+compressor`.

Promotion is mechanical: exact round-trip must pass, aggregate improvement must
exceed the threshold, and worst regression must stay under the configured
budget. Control candidates are marked `control-baseline`, not promoted.
