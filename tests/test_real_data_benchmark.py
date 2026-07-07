from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_real_data_benchmark():
    module_path = Path(__file__).resolve().parents[1] / "benchmarks" / "benchmark_real_data.py"
    spec = importlib.util.spec_from_file_location("benchmark_real_data", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_real_data_benchmark_uses_relative_labels_and_verifies(tmp_path: Path) -> None:
    benchmark = load_real_data_benchmark()
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "sample.txt").write_text("Star Light Codec\n" * 256, encoding="utf-8")
    (tmp_path / "ramp.bin").write_bytes(bytes((index * 3) % 256 for index in range(8192)))

    options = benchmark.RealDataOptions(
        paths=[tmp_path],
        label_root=tmp_path,
        max_file_bytes=1024 * 1024,
        exclude_globs=benchmark.DEFAULT_EXCLUDE_GLOBS,
        repetitions=0,
    )
    results = benchmark.build_results(options)

    assert results["summary"]["fileCount"] == 2
    assert results["summary"]["rawBytes"] > 0
    assert all(not Path(row["path"]).is_absolute() for row in results["files"])
    assert all(row["verification"] == "pass" for row in results["files"])
    assert any(row["selectedModel"] == "delta-prev-v1" for row in results["files"])


def test_real_data_benchmark_skips_large_files(tmp_path: Path) -> None:
    benchmark = load_real_data_benchmark()
    (tmp_path / "small.txt").write_text("small", encoding="utf-8")
    (tmp_path / "large.bin").write_bytes(b"x" * 16)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cache.pyc").write_bytes(b"cache")

    options = benchmark.RealDataOptions(
        paths=[tmp_path],
        label_root=tmp_path,
        max_file_bytes=8,
        exclude_globs=benchmark.DEFAULT_EXCLUDE_GLOBS,
        repetitions=0,
    )
    results = benchmark.build_results(options)

    assert [row["path"] for row in results["files"]] == ["small.txt"]
    assert any(item["reason"] == "too-large" and item["path"] == "large.bin" for item in results["skipped"])
    assert any(item["reason"] == "excluded" and item["path"].endswith("cache.pyc") for item in results["skipped"])
