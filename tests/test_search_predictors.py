from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def load_search_predictors():
    module_path = Path(__file__).resolve().parents[1] / "benchmarks" / "search_predictors.py"
    benchmark_dir = str(module_path.parent)
    if benchmark_dir not in sys.path:
        sys.path.insert(0, benchmark_dir)
    spec = importlib.util.spec_from_file_location("search_predictors", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_candidate_artifact_round_trips() -> None:
    search = load_search_predictors()
    data = bytes((index * 3) % 256 for index in range(4096))
    candidate = search.Candidate("delta-prev-1+zlib", "delta-prev", "zlib", {"offset": 1})
    artifact = search.encode_candidate(data, candidate)

    assert artifact.startswith(search.SEARCH_MAGIC)
    assert search.decode_candidate(artifact) == data


def test_predictor_search_is_bounded_and_uses_relative_labels(tmp_path: Path) -> None:
    search = load_search_predictors()
    (tmp_path / "ramp.bin").write_bytes(bytes((index * 3) % 256 for index in range(4096)))
    (tmp_path / "sample.txt").write_text("Star Light Codec\n" * 256, encoding="utf-8")

    options = search.SearchOptions(
        paths=[tmp_path],
        label_root=tmp_path,
        file_limit=2,
        candidate_limit=8,
        time_limit_seconds=5.0,
        include_file_results=True,
    )
    results = search.build_results(options)

    assert results["fileCount"] == 2
    assert results["searchMode"] == "adaptive"
    assert results["evaluatedCandidateCount"] <= 8
    assert results["stoppedReason"] in {"complete", "time-limit", "candidate-limit"}
    assert results["candidates"]
    assert results["selectionTrace"]
    assert results["modelState"]
    assert all("decision" in candidate for candidate in results["candidates"])
    assert all("selectionScore" in candidate for candidate in results["candidates"])
    for candidate in results["candidates"]:
        for row in candidate.get("files", []):
            assert not Path(row["path"]).is_absolute()


def test_time_limit_must_be_positive() -> None:
    search = load_search_predictors()

    with pytest.raises(SystemExit):
        search.parse_args(["README.md", "--time-limit-seconds", "0"])


def test_exhaustive_mode_keeps_declared_order(tmp_path: Path) -> None:
    search = load_search_predictors()
    (tmp_path / "sample.txt").write_text("Star Light Codec\n" * 128, encoding="utf-8")

    options = search.SearchOptions(
        paths=[tmp_path],
        label_root=tmp_path,
        candidate_limit=4,
        time_limit_seconds=5.0,
        search_mode="exhaustive",
    )
    results = search.build_results(options)

    assert results["searchMode"] == "exhaustive"
    assert [row["candidate"] for row in results["selectionTrace"]] == [
        "identity+gzip",
        "identity+zlib",
        "identity+bz2",
        "identity+lzma",
    ]


def test_predictor_search_state_round_trips_between_runs(tmp_path: Path) -> None:
    search = load_search_predictors()
    state_path = tmp_path / "predictor-state.json"
    (tmp_path / "sample.txt").write_text("Star Light Codec\n" * 256, encoding="utf-8")

    first = search.build_results(
        search.SearchOptions(
            paths=[tmp_path],
            label_root=tmp_path,
            candidate_limit=4,
            time_limit_seconds=5.0,
            state_output=state_path,
        )
    )
    state_doc = json.loads(state_path.read_text(encoding="utf-8"))

    assert first["stateOutput"]["written"] is True
    assert state_doc["kind"] == search.STATE_KIND
    assert state_doc["runCount"] == 1
    assert state_doc["modelState"]

    second = search.build_results(
        search.SearchOptions(
            paths=[tmp_path],
            label_root=tmp_path,
            candidate_limit=4,
            time_limit_seconds=5.0,
            state_input=state_path,
            state_output=state_path,
        )
    )
    updated_state = json.loads(state_path.read_text(encoding="utf-8"))

    assert second["inputState"]["loaded"] is True
    assert second["inputState"]["runCount"] == 1
    assert updated_state["runCount"] == 2
    assert updated_state["modelState"]
