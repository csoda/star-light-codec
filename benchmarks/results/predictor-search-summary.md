# Predictor Search Summary

These notes preserve the measured lessons from local predictor-search follow-up
runs without committing each timestamped per-case JSON artifact.

## Focused Segmented-Stream Runs

| run | candidate | average improvement | total improvement | result |
| --- | --- | ---: | ---: | --- |
| fixed selector | `segmented-stream-oracle-1024-4096-project-text-gated+zlib` | 2.539% | not recorded in summary | PASS |
| benefit-gated selector | `segmented-stream-oracle-1024-4096-project-text-benefit-gated+zlib` | 3.344% | 2.964% | PASS |
| long-token intern + benefit gate | `segmented-stream-oracle-1024-4096-project-text-long-token-intern-benefit-gated+zlib` | 2.681% | 2.336% | PASS |

## Lessons

- The benefit-gated selector outperformed the fixed selector by +0.805
  percentage points on the 10-case batch.
- The benefit gate applied 46 file-level wins and skipped 8 non-wins within 54
  project-text-gated observations; 11 files were skipped by the project-text
  gate.
- The long-token intern pre-transform did not improve the batch average:
  average improvement dropped by -0.663 percentage points versus benefit-gated
  selector alone, despite interning 43 tokens across 126 occurrences.
- `benchmarks-code` remained a useful negative control: project-text gating kept
  it at 0.000% candidate change in the benefit-gated and long-token-intern
  batches.
- Keep `predictor-search-background-latest.json` and `predictor-state.json` as
  canonical tracked machine-readable state. Treat timestamped
  `predictor-*-20*.json` and `predictor-*-20*.md` files as generated local run
  artifacts.
