from __future__ import annotations

import gzip
import hashlib
import json
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAGIC_SLB1 = b"SLB1"
MAX_PASSES = 4


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


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


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


def _compress_once(data: bytes) -> bytes:
    return gzip.compress(data, compresslevel=9, mtime=0)


def _decompress_once(data: bytes) -> bytes:
    return gzip.decompress(data)


def plan_payload(data: bytes, max_passes: int = 1) -> tuple[bytes, list[str], str, str]:
    bounded = max(1, min(MAX_PASSES, int(max_passes)))
    payload = data
    transforms: list[str] = []
    for _ in range(bounded):
        compressed = _compress_once(payload)
        if len(compressed) >= len(payload):
            break
        payload = compressed
        transforms.append("gzip")
    if not transforms:
        return payload, transforms, "stored-base64", "compression-not-beneficial"
    if len(transforms) == 1:
        return payload, transforms, "gzip-base64", ""
    return payload, transforms, "gzip-recursive-base64", ""


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


def encode_slb1(data: bytes, max_passes: int = 1) -> EncodeResult:
    payload, transforms, strategy, fallback_reason = plan_payload(data, max_passes=max_passes)
    raw_digest = sha256_digest(data)
    payload_digest = sha256_digest(payload)
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
        "fallbackReason": fallback_reason,
        "maxPasses": max(1, min(MAX_PASSES, int(max_passes))),
        "recursivePasses": len(transforms),
        "recursiveReady": True,
        "transforms": transforms,
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
        "payloadRatio": round(len(payload) / len(data), 4) if data else 0,
        "artifactRatio": round(len(artifact) / len(data), 4) if data else 0,
    }
    metadata.update(adoption_metadata(len(data), len(artifact), fallback_reason))
    return EncodeResult(metadata=metadata, artifact=artifact)


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
    if header.get("strategy") not in ("stored-base64", "gzip-base64", "gzip-recursive-base64"):
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
    if int(header.get("recursivePasses", -1)) != len(transforms):
        raise StarLightCodecError("Recursive pass count mismatch.")
    if len(transforms) > int(header.get("maxPasses", 0)):
        raise StarLightCodecError("Transform depth exceeds maxPasses.")
    if len(transforms) > MAX_PASSES:
        raise StarLightCodecError("Transform depth exceeds limit.")
    data = payload
    for transform in reversed(transforms):
        if transform != "gzip":
            raise StarLightCodecError(f"Unsupported transform: {transform}")
        data = _decompress_once(data)
    if len(data) != int(header.get("rawBytes", -1)):
        raise StarLightCodecError("Raw size mismatch.")
    if sha256_digest(data) != header.get("inputDigest"):
        raise StarLightCodecError("Input digest mismatch.")
    metadata = inspect_slb1(artifact)
    metadata["digestMatch"] = True
    return DecodeResult(metadata=metadata, data=data)


def encode_file(input_path: str | Path, output_path: str | Path, max_passes: int = 1) -> dict[str, Any]:
    data = Path(input_path).read_bytes()
    result = encode_slb1(data, max_passes=max_passes)
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
    summary: str = "",
    tags: list[str] | None = None,
    chunk_size: int = 4096,
) -> CapsuleResult:
    artifact_target = Path(artifact_path)
    capsule_target = Path(capsule_path)
    encoded = encode_slb1(data, max_passes=max_passes)
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
        "strategy": encoded.metadata["strategy"],
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
