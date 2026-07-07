from __future__ import annotations

import bz2
import gzip
import hashlib
import json
import os
import struct
import lzma
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAGIC_SLB1 = b"SLB1"
MAX_PASSES = 4
PLANNER_GZIP = "gzip"
PLANNER_STDLIB_AUTO = "stdlib-auto"
COMPRESSOR_TRANSFORMS = ("gzip", "zlib", "bz2", "lzma")
MODEL_NONE = "none"
MODEL_AUTO = "auto"
MODEL_DELTA_PREV_V1 = "delta-prev-v1"
MODEL_DELTA_PREV_V1_SPEC = (
    "star-light-codec:model:delta-prev-v1;"
    "residual=(byte-previous_byte)&0xff;previous_byte starts at 0"
)
MODEL_DELTA_PREV_V1_HASH = "sha256:" + hashlib.sha256(
    MODEL_DELTA_PREV_V1_SPEC.encode("utf-8")
).hexdigest()


class StarLightCodecError(ValueError):
    """Raised when an artifact cannot be decoded or validated."""


@dataclass(frozen=True)
class EncodeResult:
    metadata: dict[str, Any]
    artifact: bytes


@dataclass(frozen=True)
class DecodeResult:
    metadata: dict[str, Any]
    data: bytes


@dataclass(frozen=True)
class CapsuleResult:
    metadata: dict[str, Any]
    capsule: dict[str, Any]
    artifact: bytes


@dataclass(frozen=True)
class CapsulePackResult:
    metadata: dict[str, Any]
    pack: dict[str, Any]


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def estimate_prompt_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def estimate_base64_prompt_tokens(byte_count: int) -> int:
    if byte_count <= 0:
        return 0
    base64_chars = 4 * ((int(byte_count) + 2) // 3)
    return estimate_prompt_tokens("x" * base64_chars)


def _json_prompt_tokens(value: dict[str, Any]) -> int:
    compact_json = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return estimate_prompt_tokens(compact_json)


def _raw_text_prompt_tokens(data: bytes) -> int | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return estimate_prompt_tokens(text)


def _token_savings(raw_tokens: int, compact_tokens: int) -> dict[str, Any]:
    saved = max(0, raw_tokens - compact_tokens)
    ratio = round(compact_tokens / raw_tokens, 4) if raw_tokens else 0
    return {
        "estimatedTokensSavedVsBase64": saved,
        "estimatedReductionRatioVsBase64": ratio,
    }


def token_estimate_for_bytes(data: bytes) -> dict[str, Any]:
    raw_base64_tokens = estimate_base64_prompt_tokens(len(data))
    raw_text_tokens = _raw_text_prompt_tokens(data)
    return {
        "kind": "raw-bytes",
        "rawBytes": len(data),
        "rawTextPromptTokens": raw_text_tokens,
        "rawBase64PromptTokens": raw_base64_tokens,
        "compactPromptTokens": raw_base64_tokens,
        "compactKind": "raw-base64",
        "noRawPayload": False,
        **_token_savings(raw_base64_tokens, raw_base64_tokens),
    }


def token_estimate_for_document(document: dict[str, Any]) -> dict[str, Any]:
    kind = str(document.get("kind", "json-document"))
    raw_bytes = int(document.get("rawBytesTotal", document.get("rawBytes", 0)))
    compact_tokens = _json_prompt_tokens(document)
    raw_base64_tokens = estimate_base64_prompt_tokens(raw_bytes)
    return {
        "kind": kind,
        "rawBytes": raw_bytes,
        "rawTextPromptTokens": None,
        "rawBase64PromptTokens": raw_base64_tokens,
        "compactPromptTokens": compact_tokens,
        "compactKind": "capsule-pack" if kind == "slc-llm-transport-pack" else "capsule",
        "noRawPayload": True,
        **_token_savings(raw_base64_tokens, compact_tokens),
    }


def classify(data: bytes) -> str:
    if not data:
        return "empty"
    printable = sum(1 for byte in data if byte in (9, 10, 13) or 32 <= byte <= 126)
    zeroes = data.count(0)
    distinct = len(set(data))
    if printable / len(data) >= 0.88:
        return "text-like"
    if zeroes / len(data) >= 0.20 or distinct <= 16:
        return "repeated-or-sparse"
    return "binary"


def _compress_transform(data: bytes, transform: str) -> bytes:
    if transform == "gzip":
        return gzip.compress(data, compresslevel=9, mtime=0)
    if transform == "zlib":
        return zlib.compress(data, level=9)
    if transform == "bz2":
        return bz2.compress(data, compresslevel=9)
    if transform == "lzma":
        return lzma.compress(data, preset=9)
    raise StarLightCodecError(f"Unsupported compression transform: {transform}")


def _decompress_transform(data: bytes, transform: str) -> bytes:
    if transform == "gzip":
        return gzip.decompress(data)
    if transform == "zlib":
        return zlib.decompress(data)
    if transform == "bz2":
        return bz2.decompress(data)
    if transform == "lzma":
        return lzma.decompress(data)
    raise StarLightCodecError(f"Unsupported compression transform: {transform}")


def _delta_prev_encode(data: bytes) -> bytes:
    previous = 0
    output = bytearray(len(data))
    for index, byte in enumerate(data):
        output[index] = (byte - previous) & 0xFF
        previous = byte
    return bytes(output)


def _delta_prev_decode(residual: bytes) -> bytes:
    previous = 0
    output = bytearray(len(residual))
    for index, delta in enumerate(residual):
        byte = (previous + delta) & 0xFF
        output[index] = byte
        previous = byte
    return bytes(output)


def _plan_gzip_transforms(data: bytes, max_passes: int) -> tuple[bytes, list[str]]:
    bounded = max(1, min(MAX_PASSES, int(max_passes)))
    payload = data
    transforms: list[str] = []
    for _ in range(bounded):
        compressed = _compress_transform(payload, "gzip")
        if len(compressed) >= len(payload):
            break
        payload = compressed
        transforms.append("gzip")
    return payload, transforms


def _plan_stdlib_transforms(data: bytes, max_passes: int) -> tuple[bytes, list[str]]:
    candidates: list[tuple[bytes, list[str]]] = [(data, [])]
    candidates.append(_plan_gzip_transforms(data, max_passes=max_passes))
    for transform in ("zlib", "bz2", "lzma"):
        compressed = _compress_transform(data, transform)
        if len(compressed) < len(data):
            candidates.append((compressed, [transform]))
    return min(candidates, key=lambda candidate: len(candidate[0]))


def _plan_compressor_transforms(data: bytes, max_passes: int, planner: str) -> tuple[bytes, list[str]]:
    normalized_planner = _validate_planner_name(planner)
    if normalized_planner == PLANNER_GZIP:
        return _plan_gzip_transforms(data, max_passes=max_passes)
    return _plan_stdlib_transforms(data, max_passes=max_passes)


def _strategy_for_transforms(transforms: list[str]) -> str:
    has_model = MODEL_DELTA_PREV_V1 in transforms
    compression_transforms = [transform for transform in transforms if transform in COMPRESSOR_TRANSFORMS]
    prefix = "delta-prev-" if has_model else ""
    if not compression_transforms:
        return f"{prefix}stored-base64" if prefix else "stored-base64"
    if len(compression_transforms) == 1:
        return f"{prefix}{compression_transforms[0]}-base64"
    if len(set(compression_transforms)) == 1:
        return f"{prefix}{compression_transforms[0]}-recursive-base64"
    return f"{prefix}mixed-recursive-base64"


def _validate_planner_name(planner: str) -> str:
    normalized = (planner or PLANNER_GZIP).strip().lower()
    if normalized not in (PLANNER_GZIP, PLANNER_STDLIB_AUTO):
        raise StarLightCodecError("Unsupported compression planner.")
    return normalized


def _validate_model_name(model: str) -> str:
    normalized = (model or MODEL_NONE).strip().lower()
    if normalized not in (MODEL_NONE, MODEL_AUTO, MODEL_DELTA_PREV_V1):
        raise StarLightCodecError("Unsupported prediction model.")
    return normalized


def _fallback_reason(model: str, transforms: list[str]) -> str:
    if any(transform in COMPRESSOR_TRANSFORMS for transform in transforms):
        return ""
    if model == MODEL_DELTA_PREV_V1:
        return "model-compression-not-beneficial"
    return "compression-not-beneficial"


def _count_compressor_passes(transforms: list[str]) -> int:
    return sum(1 for transform in transforms if transform in COMPRESSOR_TRANSFORMS)


def _supported_strategy_names() -> set[str]:
    names = {"stored-base64", "delta-prev-stored-base64"}
    for transform in COMPRESSOR_TRANSFORMS:
        names.add(f"{transform}-base64")
        names.add(f"{transform}-recursive-base64")
        names.add(f"delta-prev-{transform}-base64")
        names.add(f"delta-prev-{transform}-recursive-base64")
    names.add("mixed-recursive-base64")
    names.add("delta-prev-mixed-recursive-base64")
    return names


def _validate_transform_stack(transforms: list[str]) -> None:
    for transform in transforms:
        if transform not in (*COMPRESSOR_TRANSFORMS, MODEL_DELTA_PREV_V1):
            raise StarLightCodecError(f"Unsupported transform: {transform}")
    model_passes = sum(1 for transform in transforms if transform == MODEL_DELTA_PREV_V1)
    if model_passes > 1:
        raise StarLightCodecError("Prediction model transform depth exceeds limit.")
    if MODEL_DELTA_PREV_V1 in transforms and transforms[0] != MODEL_DELTA_PREV_V1:
        raise StarLightCodecError("Prediction model transform order is invalid.")


def _legacy_strategy_for_transforms(transforms: list[str]) -> str:
    gzip_count = sum(1 for transform in transforms if transform == "gzip")
    has_model = MODEL_DELTA_PREV_V1 in transforms
    if not transforms:
        return "stored-base64"
    if has_model and gzip_count == 0:
        return "delta-prev-stored-base64"
    if has_model and gzip_count == 1:
        return "delta-prev-gzip-base64"
    if has_model:
        return "delta-prev-gzip-recursive-base64"
    if gzip_count == 1:
        return "gzip-base64"
    return "gzip-recursive-base64"


def _model_metadata(model: str) -> dict[str, str]:
    if model == MODEL_DELTA_PREV_V1:
        return {
            "modelId": MODEL_DELTA_PREV_V1,
            "modelKind": "predictive-residual",
            "modelHash": MODEL_DELTA_PREV_V1_HASH,
            "residualKind": "delta-from-previous-byte",
        }
    return {"modelId": MODEL_NONE}


def _plan_payload_once(
    data: bytes,
    max_passes: int = 1,
    model: str = MODEL_NONE,
    planner: str = PLANNER_GZIP,
) -> tuple[bytes, list[str], str, str, str, str]:
    normalized_model = _validate_model_name(model)
    normalized_planner = _validate_planner_name(planner)
    if normalized_model == MODEL_AUTO:
        raise StarLightCodecError("Internal payload planner does not accept auto model selection.")
    if normalized_model == MODEL_NONE:
        payload, transforms = _plan_compressor_transforms(
            data,
            max_passes=max_passes,
            planner=normalized_planner,
        )
        return (
            payload,
            transforms,
            _strategy_for_transforms(transforms),
            _fallback_reason(normalized_model, transforms),
            MODEL_NONE,
            normalized_planner,
        )
    residual = _delta_prev_encode(data)
    payload, compressor_transforms = _plan_compressor_transforms(
        residual,
        max_passes=max_passes,
        planner=normalized_planner,
    )
    transforms = [MODEL_DELTA_PREV_V1, *compressor_transforms]
    return (
        payload,
        transforms,
        _strategy_for_transforms(transforms),
        _fallback_reason(normalized_model, transforms),
        MODEL_DELTA_PREV_V1,
        normalized_planner,
    )


def plan_payload(
    data: bytes,
    max_passes: int = 1,
    model: str = MODEL_NONE,
    planner: str = PLANNER_GZIP,
) -> tuple[bytes, list[str], str, str, str, str]:
    normalized_model = _validate_model_name(model)
    normalized_planner = _validate_planner_name(planner)
    if normalized_model != MODEL_AUTO:
        return _plan_payload_once(
            data,
            max_passes=max_passes,
            model=normalized_model,
            planner=normalized_planner,
        )
    baseline = _plan_payload_once(data, max_passes=max_passes, model=MODEL_NONE, planner=normalized_planner)
    modeled = _plan_payload_once(
        data,
        max_passes=max_passes,
        model=MODEL_DELTA_PREV_V1,
        planner=normalized_planner,
    )
    if len(modeled[0]) < len(baseline[0]):
        return modeled
    return baseline


def adoption_metadata(raw_bytes: int, artifact_bytes: int, fallback_reason: str) -> dict[str, Any]:
    if raw_bytes <= 0:
        recommended = False
        reason = "empty-input-has-no-storage-savings"
    else:
        recommended = artifact_bytes < raw_bytes
        if recommended:
            reason = "whole-artifact-smaller-than-input"
        elif fallback_reason:
            reason = "payload-fallback-did-not-make-whole-artifact-smaller"
        else:
            reason = "whole-artifact-not-smaller-than-input"
    return {
        "recommendedForStorage": recommended,
        "adoptionDecision": "use-artifact-for-storage" if recommended else "keep-original-for-storage",
        "adoptionReason": reason,
    }


def _encode_slb1_once(
    data: bytes,
    max_passes: int = 1,
    model: str = MODEL_NONE,
    planner: str = PLANNER_GZIP,
) -> EncodeResult:
    payload, transforms, strategy, fallback_reason, selected_model, selected_planner = plan_payload(
        data,
        max_passes=max_passes,
        model=model,
        planner=planner,
    )
    raw_digest = sha256_digest(data)
    payload_digest = sha256_digest(payload)
    compressor_passes = _count_compressor_passes(transforms)
    header = {
        "schemaVersion": 2,
        "feature": "semantic-codec",
        "packageKind": "starlight-byte-exact",
        "packageFormat": "layered",
        "artifactContainer": "slb1",
        "container": "slb1",
        "layered": True,
        "mode": "exact",
        "codec": "starlight-byte-exact",
        "prototype": True,
        "strategy": strategy,
        "classification": classify(data),
        "planner": selected_planner,
        "fallbackReason": fallback_reason,
        "maxPasses": max(1, min(MAX_PASSES, int(max_passes))),
        "recursivePasses": compressor_passes,
        "recursiveReady": True,
        "transforms": transforms,
        "predictionModel": _model_metadata(selected_model),
        "rawBytes": len(data),
        "payloadBytes": len(payload),
        "inputDigest": raw_digest,
        "payloadDigest": payload_digest,
        "layers": [
            {
                "name": "payload",
                "encoding": "raw",
                "rawBytes": len(data),
                "storedBytes": len(payload),
                "digest": payload_digest,
                "transforms": transforms,
            }
        ],
    }
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    artifact = MAGIC_SLB1 + struct.pack("<IQ", len(header_bytes), len(payload)) + header_bytes + payload
    metadata = {
        **header,
        "artifactBytes": len(artifact),
        "packageBytes": len(artifact),
        "selectedStrategy": strategy,
        "selectedModel": selected_model,
        "selectedPlanner": selected_planner,
        "payloadRatio": round(len(payload) / len(data), 4) if data else 0,
        "artifactRatio": round(len(artifact) / len(data), 4) if data else 0,
    }
    metadata.update(adoption_metadata(len(data), len(artifact), fallback_reason))
    return EncodeResult(metadata=metadata, artifact=artifact)


def encode_slb1(
    data: bytes,
    max_passes: int = 1,
    model: str = MODEL_NONE,
    planner: str = PLANNER_GZIP,
) -> EncodeResult:
    normalized_model = _validate_model_name(model)
    normalized_planner = _validate_planner_name(planner)
    candidate_models = (
        [MODEL_NONE, MODEL_DELTA_PREV_V1] if normalized_model == MODEL_AUTO else [normalized_model]
    )
    candidate_planners = (
        [PLANNER_GZIP, PLANNER_STDLIB_AUTO]
        if normalized_planner == PLANNER_STDLIB_AUTO
        else [PLANNER_GZIP]
    )
    candidates = [
        _encode_slb1_once(
            data,
            max_passes=max_passes,
            model=candidate_model,
            planner=candidate_planner,
        )
        for candidate_planner in candidate_planners
        for candidate_model in candidate_models
    ]
    return min(candidates, key=lambda candidate: len(candidate.artifact))


def read_slb1(artifact: bytes) -> tuple[dict[str, Any], bytes]:
    if len(artifact) < 16:
        raise StarLightCodecError("SLB1 artifact is truncated.")
    if artifact[:4] != MAGIC_SLB1:
        raise StarLightCodecError("SLB1 magic mismatch.")
    header_len, payload_len = struct.unpack("<IQ", artifact[4:16])
    expected_len = 16 + header_len + payload_len
    if header_len <= 0 or payload_len < 0 or expected_len != len(artifact):
        raise StarLightCodecError("SLB1 length mismatch.")
    try:
        header = json.loads(artifact[16 : 16 + header_len].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StarLightCodecError("SLB1 header is not valid UTF-8 JSON.") from exc
    payload = artifact[16 + header_len :]
    return header, payload


def inspect_slb1(artifact: bytes) -> dict[str, Any]:
    header, payload = read_slb1(artifact)
    if "data" in header:
        raise StarLightCodecError("SLB1 header must not embed top-level data.")
    metadata = dict(header)
    metadata["artifactBytes"] = len(artifact)
    metadata["payloadDigestMatch"] = sha256_digest(payload) == header.get("payloadDigest")
    metadata.update(
        adoption_metadata(
            int(header.get("rawBytes", 0)),
            len(artifact),
            str(header.get("fallbackReason", "")),
        )
    )
    return metadata


def decode_slb1(artifact: bytes) -> DecodeResult:
    header, payload = read_slb1(artifact)
    if int(header.get("schemaVersion", -1)) != 2:
        raise StarLightCodecError("Unsupported schema version.")
    if header.get("codec") != "starlight-byte-exact":
        raise StarLightCodecError("Unsupported codec.")
    if header.get("packageKind") != "starlight-byte-exact":
        raise StarLightCodecError("Unsupported package kind.")
    if header.get("packageFormat") != "layered":
        raise StarLightCodecError("Unsupported package format.")
    if header.get("artifactContainer") != "slb1":
        raise StarLightCodecError("Unsupported artifact container.")
    if header.get("container", "slb1") != "slb1":
        raise StarLightCodecError("Unsupported container.")
    if "data" in header:
        raise StarLightCodecError("SLB1 header must not embed top-level data.")
    if header.get("strategy") not in _supported_strategy_names():
        raise StarLightCodecError("Unsupported strategy.")
    if int(header.get("payloadBytes", -1)) != len(payload):
        raise StarLightCodecError("Payload size mismatch.")
    if sha256_digest(payload) != header.get("payloadDigest"):
        raise StarLightCodecError("Payload digest mismatch.")
    payload_layers = [layer for layer in header.get("layers", []) if layer.get("name") == "payload"]
    if len(payload_layers) != 1:
        raise StarLightCodecError("Payload layer is missing or duplicated.")
    payload_layer = payload_layers[0]
    if payload_layer.get("encoding") != "raw":
        raise StarLightCodecError("Unsupported payload layer encoding.")
    if "data" in payload_layer:
        raise StarLightCodecError("Payload layer must not embed raw data in the header.")
    if int(payload_layer.get("storedBytes", -1)) != len(payload):
        raise StarLightCodecError("Payload layer size mismatch.")
    if payload_layer.get("digest") != header.get("payloadDigest"):
        raise StarLightCodecError("Payload layer digest mismatch.")
    transforms = list(header.get("transforms", []))
    if transforms != list(payload_layer.get("transforms", [])):
        raise StarLightCodecError("Payload layer transform mismatch.")
    _validate_transform_stack(transforms)
    if header.get("strategy") not in (
        _strategy_for_transforms(transforms),
        _legacy_strategy_for_transforms(transforms),
    ):
        raise StarLightCodecError("Strategy does not match transform stack.")
    prediction_model = header.get("predictionModel", {"modelId": MODEL_NONE})
    if not isinstance(prediction_model, dict):
        raise StarLightCodecError("Invalid prediction model metadata.")
    model_id = str(prediction_model.get("modelId", MODEL_NONE))
    if model_id not in (MODEL_NONE, MODEL_DELTA_PREV_V1):
        raise StarLightCodecError("Unsupported prediction model.")
    if model_id == MODEL_DELTA_PREV_V1:
        if prediction_model.get("modelHash") != MODEL_DELTA_PREV_V1_HASH:
            raise StarLightCodecError("Prediction model hash mismatch.")
        if MODEL_DELTA_PREV_V1 not in transforms:
            raise StarLightCodecError("Prediction model transform is missing.")
    if MODEL_DELTA_PREV_V1 in transforms and model_id != MODEL_DELTA_PREV_V1:
        raise StarLightCodecError("Prediction model metadata is missing.")
    compressor_passes = _count_compressor_passes(transforms)
    if int(header.get("recursivePasses", -1)) != compressor_passes:
        raise StarLightCodecError("Recursive pass count mismatch.")
    if compressor_passes > int(header.get("maxPasses", 0)):
        raise StarLightCodecError("Transform depth exceeds maxPasses.")
    if compressor_passes > MAX_PASSES:
        raise StarLightCodecError("Transform depth exceeds limit.")
    data = payload
    for transform in reversed(transforms):
        if transform in COMPRESSOR_TRANSFORMS:
            data = _decompress_transform(data, transform)
        elif transform == MODEL_DELTA_PREV_V1:
            data = _delta_prev_decode(data)
    if len(data) != int(header.get("rawBytes", -1)):
        raise StarLightCodecError("Raw size mismatch.")
    if sha256_digest(data) != header.get("inputDigest"):
        raise StarLightCodecError("Input digest mismatch.")
    metadata = inspect_slb1(artifact)
    metadata["digestMatch"] = True
    return DecodeResult(metadata=metadata, data=data)


def encode_file(
    input_path: str | Path,
    output_path: str | Path,
    max_passes: int = 1,
    model: str = MODEL_NONE,
    planner: str = PLANNER_GZIP,
) -> dict[str, Any]:
    data = Path(input_path).read_bytes()
    result = encode_slb1(data, max_passes=max_passes, model=model, planner=planner)
    Path(output_path).write_bytes(result.artifact)
    return result.metadata


def decode_file(input_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    artifact = Path(input_path).read_bytes()
    result = decode_slb1(artifact)
    Path(output_path).write_bytes(result.data)
    return result.metadata


def parse_byte_range(range_spec: str, raw_bytes: int) -> tuple[int, int]:
    if ":" not in range_spec:
        raise StarLightCodecError("Byte range must use start:end syntax.")
    start_text, end_text = range_spec.split(":", 1)
    start = int(start_text) if start_text else 0
    end = int(end_text) if end_text else raw_bytes
    if start < 0 or end < start or end > raw_bytes:
        raise StarLightCodecError("Byte range is outside the decoded data.")
    return start, end


def build_chunk_index(data: bytes, chunk_size: int = 4096) -> list[dict[str, Any]]:
    bounded_size = max(1, int(chunk_size))
    chunks: list[dict[str, Any]] = []
    for index, start in enumerate(range(0, len(data), bounded_size), start=1):
        end = min(start + bounded_size, len(data))
        chunk = data[start:end]
        chunks.append(
            {
                "chunkId": f"c{index:04d}",
                "start": start,
                "end": end,
                "rawBytes": len(chunk),
                "digest": sha256_digest(chunk),
            }
        )
    return chunks


def _relative_artifact_ref(artifact_path: Path, capsule_path: Path) -> str:
    base = capsule_path.parent if capsule_path.parent != Path("") else Path(".")
    return os.path.relpath(artifact_path, base).replace("\\", "/")


def create_capsule(
    data: bytes,
    artifact_path: str | Path,
    capsule_path: str | Path,
    max_passes: int = 1,
    model: str = MODEL_NONE,
    planner: str = PLANNER_GZIP,
    summary: str = "",
    tags: list[str] | None = None,
    chunk_size: int = 4096,
) -> CapsuleResult:
    artifact_target = Path(artifact_path)
    capsule_target = Path(capsule_path)
    encoded = encode_slb1(data, max_passes=max_passes, model=model, planner=planner)
    normalized_tags = sorted({tag.strip() for tag in (tags or []) if tag.strip()})
    artifact_ref = _relative_artifact_ref(artifact_target, capsule_target)
    capsule = {
        "schemaVersion": 1,
        "kind": "slc-llm-transport",
        "artifactRef": artifact_ref,
        "artifactContainer": "slb1",
        "artifactProfile": "starlight-byte-exact",
        "artifactDigest": sha256_digest(encoded.artifact),
        "artifactBytes": len(encoded.artifact),
        "rawBytes": len(data),
        "inputDigest": encoded.metadata["inputDigest"],
        "classification": encoded.metadata["classification"],
        "planner": encoded.metadata["planner"],
        "strategy": encoded.metadata["strategy"],
        "selectedPlanner": encoded.metadata["selectedPlanner"],
        "selectedModel": encoded.metadata["selectedModel"],
        "predictionModel": encoded.metadata["predictionModel"],
        "transforms": list(encoded.metadata["transforms"]),
        "recommendedForStorage": encoded.metadata["recommendedForStorage"],
        "adoptionDecision": encoded.metadata["adoptionDecision"],
        "summary": summary,
        "semanticTags": normalized_tags,
        "chunkIndex": build_chunk_index(data, chunk_size=chunk_size),
        "hydrate": {
            "tool": "slc hydrate",
            "supports": ["full", "range", "chunk"],
            "rangeSyntax": "start:end",
        },
    }
    return CapsuleResult(
        metadata={
            "action": "capsule",
            "kind": capsule["kind"],
            "artifactRef": artifact_ref,
            "artifactBytes": len(encoded.artifact),
            "rawBytes": len(data),
            "inputDigest": encoded.metadata["inputDigest"],
            "artifactDigest": capsule["artifactDigest"],
            "chunkCount": len(capsule["chunkIndex"]),
            "selectedPlanner": encoded.metadata["selectedPlanner"],
            "selectedModel": encoded.metadata["selectedModel"],
            "recommendedForStorage": encoded.metadata["recommendedForStorage"],
            "adoptionDecision": encoded.metadata["adoptionDecision"],
        },
        capsule=capsule,
        artifact=encoded.artifact,
    )


def create_capsule_file(
    input_path: str | Path,
    artifact_path: str | Path,
    capsule_path: str | Path,
    max_passes: int = 1,
    model: str = MODEL_NONE,
    planner: str = PLANNER_GZIP,
    summary: str = "",
    tags: list[str] | None = None,
    chunk_size: int = 4096,
) -> dict[str, Any]:
    data = Path(input_path).read_bytes()
    result = create_capsule(
        data,
        artifact_path=artifact_path,
        capsule_path=capsule_path,
        max_passes=max_passes,
        model=model,
        planner=planner,
        summary=summary,
        tags=tags,
        chunk_size=chunk_size,
    )
    artifact_target = Path(artifact_path)
    capsule_target = Path(capsule_path)
    artifact_target.parent.mkdir(parents=True, exist_ok=True)
    capsule_target.parent.mkdir(parents=True, exist_ok=True)
    artifact_target.write_bytes(result.artifact)
    capsule_target.write_text(json.dumps(result.capsule, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result.metadata


def _relative_json_ref(source_path: Path, output_path: Path) -> str:
    base = output_path.parent if output_path.parent != Path("") else Path(".")
    return os.path.relpath(source_path, base).replace("\\", "/")


def _pack_item_from_capsule(capsule_path: Path, output_path: Path, capsule: dict[str, Any], index: int) -> dict[str, Any]:
    chunk_index = capsule.get("chunkIndex", [])
    chunk_count = len(chunk_index) if isinstance(chunk_index, list) else 0
    return {
        "itemId": f"i{index:04d}",
        "kind": "capsule",
        "capsuleRef": _relative_json_ref(capsule_path, output_path),
        "artifactRef": capsule.get("artifactRef", ""),
        "artifactDigest": capsule.get("artifactDigest", ""),
        "artifactBytes": int(capsule.get("artifactBytes", 0)),
        "rawBytes": int(capsule.get("rawBytes", 0)),
        "inputDigest": capsule.get("inputDigest", ""),
        "classification": capsule.get("classification", ""),
        "strategy": capsule.get("strategy", ""),
        "selectedPlanner": capsule.get("selectedPlanner", ""),
        "selectedModel": capsule.get("selectedModel", ""),
        "summary": capsule.get("summary", ""),
        "semanticTags": list(capsule.get("semanticTags", [])),
        "chunkCount": chunk_count,
        "hydrate": {
            "tool": "slc hydrate",
            "source": _relative_json_ref(capsule_path, output_path),
            "supports": ["full", "range", "chunk"],
        },
    }


def _pack_item_from_pack(pack_path: Path, output_path: Path, pack: dict[str, Any], index: int) -> dict[str, Any]:
    items = pack.get("items", [])
    item_count = len(items) if isinstance(items, list) else 0
    return {
        "itemId": f"i{index:04d}",
        "kind": "pack",
        "packRef": _relative_json_ref(pack_path, output_path),
        "rawBytesTotal": int(pack.get("rawBytesTotal", 0)),
        "artifactBytesTotal": int(pack.get("artifactBytesTotal", 0)),
        "summary": pack.get("summary", ""),
        "semanticTags": list(pack.get("semanticTags", [])),
        "itemCount": item_count,
    }


FORBIDDEN_TRANSPORT_PAYLOAD_FIELDS = {
    "base64Payload",
    "bytes",
    "data",
    "payload",
    "payloadBase64",
    "rawPayload",
}
MAX_PACK_REFERENCE_DEPTH = 16


def _validate_no_raw_payload_fields(value: Any) -> None:
    if isinstance(value, dict):
        if FORBIDDEN_TRANSPORT_PAYLOAD_FIELDS.intersection(value):
            raise StarLightCodecError("Transport documents must not embed raw payload fields.")
        for child in value.values():
            _validate_no_raw_payload_fields(child)
    elif isinstance(value, list):
        for child in value:
            _validate_no_raw_payload_fields(child)


def _validate_transport_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise StarLightCodecError("Transport document must be a JSON object.")
    try:
        _validate_no_raw_payload_fields(document)
    except RecursionError as exc:
        raise StarLightCodecError("Transport documents must not embed raw payload fields.") from exc
    kind = document.get("kind")
    if kind not in ("slc-llm-transport", "slc-llm-transport-pack"):
        raise StarLightCodecError("Unsupported transport document kind.")
    if document.get("schemaVersion") != 1:
        raise StarLightCodecError("Unsupported transport document schema version.")
    return document


def read_transport_document(path: str | Path) -> dict[str, Any]:
    source_path = Path(path)
    try:
        document = json.loads(source_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StarLightCodecError("Transport document is not valid UTF-8 JSON.") from exc
    return _validate_transport_document(document)


def _resolve_pack_ref(pack_path: Path, pack_ref: str) -> Path:
    if not pack_ref:
        raise StarLightCodecError("Capsule pack item is missing packRef.")
    if Path(pack_ref).is_absolute():
        raise StarLightCodecError("Capsule pack references must be relative.")
    return (pack_path.parent / pack_ref).resolve()


def _validate_pack_reference_graph(
    pack_path: Path,
    output_path: Path,
    visiting: set[Path],
    visited: set[Path],
    depth: int = 0,
) -> None:
    resolved_pack = pack_path.resolve()
    resolved_output = output_path.resolve()
    if resolved_pack == resolved_output:
        raise StarLightCodecError("Capsule pack must not reference itself.")
    if depth > MAX_PACK_REFERENCE_DEPTH:
        raise StarLightCodecError("Capsule pack reference depth exceeds limit.")
    if resolved_pack in visiting:
        raise StarLightCodecError("Capsule pack reference cycle detected.")
    if resolved_pack in visited:
        return
    document = read_transport_document(resolved_pack)
    if document.get("kind") != "slc-llm-transport-pack":
        visited.add(resolved_pack)
        return
    visiting.add(resolved_pack)
    items = document.get("items", [])
    if not isinstance(items, list):
        raise StarLightCodecError("Capsule pack items must be a list.")
    for item in items:
        if not isinstance(item, dict):
            raise StarLightCodecError("Capsule pack items must be objects.")
        if item.get("kind") == "pack":
            child_path = _resolve_pack_ref(resolved_pack, str(item.get("packRef", "")))
            _validate_pack_reference_graph(child_path, resolved_output, visiting, visited, depth + 1)
    visiting.remove(resolved_pack)
    visited.add(resolved_pack)


def _validate_capsule_pack_inputs(input_paths: list[str | Path], output_path: Path) -> None:
    visited: set[Path] = set()
    for input_path in input_paths:
        source_path = Path(input_path).resolve()
        if source_path == output_path.resolve():
            raise StarLightCodecError("Capsule pack must not reference itself.")
        _validate_pack_reference_graph(source_path, output_path, set(), visited)


def create_capsule_pack(
    input_paths: list[str | Path],
    output_path: str | Path,
    summary: str = "",
    tags: list[str] | None = None,
) -> CapsulePackResult:
    pack_target = Path(output_path)
    _validate_capsule_pack_inputs(input_paths, pack_target)
    normalized_tags = sorted({tag.strip() for tag in (tags or []) if tag.strip()})
    items: list[dict[str, Any]] = []
    for index, input_path in enumerate(input_paths, start=1):
        source_path = Path(input_path)
        document = read_transport_document(source_path)
        if document.get("kind") == "slc-llm-transport":
            items.append(_pack_item_from_capsule(source_path, pack_target, document, index))
        else:
            items.append(_pack_item_from_pack(source_path, pack_target, document, index))
    raw_bytes_total = sum(int(item.get("rawBytes", item.get("rawBytesTotal", 0))) for item in items)
    artifact_bytes_total = sum(int(item.get("artifactBytes", item.get("artifactBytesTotal", 0))) for item in items)
    pack = {
        "schemaVersion": 1,
        "kind": "slc-llm-transport-pack",
        "summary": summary,
        "semanticTags": normalized_tags,
        "itemCount": len(items),
        "rawBytesTotal": raw_bytes_total,
        "artifactBytesTotal": artifact_bytes_total,
        "items": items,
        "hydrate": {
            "tool": "slc hydrate",
            "note": "Hydrate exact bytes from referenced capsules; packs do not embed payload bytes.",
        },
        "tokenEstimate": {},
    }
    pack["tokenEstimate"] = token_estimate_for_document(pack)
    return CapsulePackResult(
        metadata={
            "action": "capsule-pack",
            "kind": pack["kind"],
            "itemCount": len(items),
            "rawBytesTotal": raw_bytes_total,
            "artifactBytesTotal": artifact_bytes_total,
            "tokenEstimate": pack["tokenEstimate"],
        },
        pack=pack,
    )


def create_capsule_pack_file(
    input_paths: list[str | Path],
    output_path: str | Path,
    summary: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    result = create_capsule_pack(input_paths, output_path=output_path, summary=summary, tags=tags)
    pack_target = Path(output_path)
    pack_target.parent.mkdir(parents=True, exist_ok=True)
    pack_target.write_text(json.dumps(result.pack, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result.metadata


def token_report_file(input_path: str | Path) -> dict[str, Any]:
    source_path = Path(input_path)
    probe = source_path.read_bytes()
    try:
        document = json.loads(probe.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        report = token_estimate_for_bytes(probe)
        report["sourceKind"] = "raw-file"
        return report
    if isinstance(document, dict) and document.get("kind") in ("slc-llm-transport", "slc-llm-transport-pack"):
        transport_document = _validate_transport_document(document)
        report = token_estimate_for_document(transport_document)
        report["sourceKind"] = str(transport_document.get("kind"))
        return report
    report = token_estimate_for_bytes(probe)
    report["sourceKind"] = "raw-json-file"
    return report


def _read_capsule(path: Path) -> dict[str, Any]:
    try:
        capsule = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StarLightCodecError("Capsule is not valid UTF-8 JSON.") from exc
    if capsule.get("kind") != "slc-llm-transport":
        raise StarLightCodecError("Unsupported capsule kind.")
    if capsule.get("schemaVersion") != 1:
        raise StarLightCodecError("Unsupported capsule schema version.")
    if "data" in capsule:
        raise StarLightCodecError("Capsule must not embed raw data.")
    return capsule


def _resolve_capsule_artifact(capsule_path: Path, capsule: dict[str, Any]) -> Path:
    artifact_ref = str(capsule.get("artifactRef", ""))
    if not artifact_ref:
        raise StarLightCodecError("Capsule is missing artifactRef.")
    if Path(artifact_ref).is_absolute():
        raise StarLightCodecError("Capsule artifactRef must be relative.")
    return (capsule_path.parent / artifact_ref).resolve()


def _read_artifact_or_capsule(input_path: Path) -> tuple[bytes, dict[str, Any] | None]:
    probe = input_path.read_bytes()
    if probe.startswith(MAGIC_SLB1):
        return probe, None
    capsule = _read_capsule(input_path)
    artifact_path = _resolve_capsule_artifact(input_path, capsule)
    artifact = artifact_path.read_bytes()
    if sha256_digest(artifact) != capsule.get("artifactDigest"):
        raise StarLightCodecError("Capsule artifact digest mismatch.")
    return artifact, capsule


def _chunk_from_capsule(capsule: dict[str, Any], chunk_id: str) -> dict[str, Any]:
    for chunk in capsule.get("chunkIndex", []):
        if chunk.get("chunkId") == chunk_id:
            return chunk
    raise StarLightCodecError("Chunk id was not found in the capsule.")


def hydrate_file(
    input_path: str | Path,
    output_path: str | Path,
    byte_range: str | None = None,
    chunk_id: str | None = None,
) -> dict[str, Any]:
    if byte_range and chunk_id:
        raise StarLightCodecError("Use either byte_range or chunk_id, not both.")
    source_path = Path(input_path)
    artifact, capsule = _read_artifact_or_capsule(source_path)
    decoded = decode_slb1(artifact)
    start, end = 0, len(decoded.data)
    hydrate_mode = "full"
    expected_output_digest = ""
    if chunk_id:
        if capsule is None:
            raise StarLightCodecError("Chunk hydration requires a capsule input.")
        chunk = _chunk_from_capsule(capsule, chunk_id)
        start, end = int(chunk["start"]), int(chunk["end"])
        expected_output_digest = str(chunk.get("digest", ""))
        hydrate_mode = "chunk"
    elif byte_range:
        start, end = parse_byte_range(byte_range, len(decoded.data))
        hydrate_mode = "range"
    if start < 0 or end < start or end > len(decoded.data):
        raise StarLightCodecError("Hydration range is outside the decoded data.")
    hydrated = decoded.data[start:end]
    output_digest = sha256_digest(hydrated)
    if expected_output_digest and output_digest != expected_output_digest:
        raise StarLightCodecError("Capsule chunk digest mismatch.")
    Path(output_path).write_bytes(hydrated)
    return {
        "action": "hydrate",
        "hydrateMode": hydrate_mode,
        "chunkId": chunk_id or "",
        "start": start,
        "end": end,
        "outputBytes": len(hydrated),
        "outputDigest": output_digest,
        "inputDigest": decoded.metadata["inputDigest"],
        "artifactDigest": sha256_digest(artifact),
        "sourceKind": "capsule" if capsule is not None else "slb1",
    }
