from __future__ import annotations

import gzip
import hashlib
import json
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
