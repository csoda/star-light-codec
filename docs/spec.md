# Star Light Codec Specification

This document describes the first public Star Light Codec artifact format:
`SLB1`, using the `starlight-byte-exact` compatibility profile from Star Light.

Status: experimental reference specification.

## Goals

`SLB1` is an exact byte artifact container. It is designed to keep the decoder
small, auditable, and deterministic while allowing encoder planning to improve
over time.

The current format preserves:

- original byte length;
- transformed payload length;
- SHA-256 digest of the original input;
- SHA-256 digest of the transformed payload;
- a bounded transform stack;
- enough metadata to decide whether storing the artifact is useful.

## Binary Layout

All integer fields are little-endian.

| Field | Size | Meaning |
| --- | ---: | --- |
| `magic` | 4 bytes | ASCII `SLB1` |
| `headerLength` | 4 bytes | unsigned 32-bit length of the UTF-8 JSON header |
| `payloadLength` | 8 bytes | unsigned 64-bit length of the raw transformed payload |
| `header` | variable | compact UTF-8 JSON |
| `payload` | variable | raw transformed payload bytes |

The artifact length must equal:

```text
16 + headerLength + payloadLength
```

Decoders must fail closed on length mismatch, invalid UTF-8, invalid JSON,
unsupported transforms, digest mismatch, or raw-size mismatch.

## Header

The current header is a JSON object with these required fields:

| Field | Meaning |
| --- | --- |
| `schemaVersion` | Current value: `2` |
| `feature` | Current value: `semantic-codec` |
| `packageKind` | Current value: `starlight-byte-exact` |
| `packageFormat` | Current value: `layered` |
| `artifactContainer` | Current value: `slb1` |
| `container` | Optional convenience field. If present, current value: `slb1` |
| `layered` | Current value: `true` |
| `mode` | Current value: `exact` |
| `codec` | Current value: `starlight-byte-exact` |
| `prototype` | Current value: `true` |
| `strategy` | `stored-base64`, `gzip-base64`, or `gzip-recursive-base64` |
| `classification` | Input shape hint such as `text-like`, `binary`, or `empty` |
| `fallbackReason` | Reason compression was not adopted for the payload, if any |
| `maxPasses` | Encoder's bounded transform limit, 1 through 4 |
| `recursivePasses` | Count of applied transforms |
| `recursiveReady` | Current value: `true` |
| `transforms` | Ordered transform names applied to the payload |
| `rawBytes` | Original byte length |
| `payloadBytes` | Transformed payload byte length |
| `inputDigest` | `sha256:<64 hex>` digest of original bytes |
| `payloadDigest` | `sha256:<64 hex>` digest of transformed payload |
| `layers` | Layer metadata; currently one raw payload layer |

The only transform in version 0.1 is `gzip`. The strategy names include
`base64` because they are shared with Star Light's JSON package profile. In
`SLB1`, the payload bytes are still stored raw outside the JSON header.

## Decode Algorithm

1. Verify the `SLB1` magic.
2. Read `headerLength` and `payloadLength`.
3. Verify the artifact length.
4. Parse the header as UTF-8 JSON.
5. Verify schema, package kind, container, lengths, and digest shapes.
6. Verify `sha256(payload) == payloadDigest`.
7. Apply transforms in reverse order.
8. Verify raw byte length.
9. Verify `sha256(raw) == inputDigest`.
10. Return the exact raw bytes.

## Storage Adoption

The reference encoder reports storage advice:

| Decision | Meaning |
| --- | --- |
| `use-artifact-for-storage` | Whole artifact is smaller than the input. |
| `keep-original-for-storage` | Whole artifact is empty, equal, larger, or a compression fallback. |

This advice is metadata, not a decoder requirement.

## Compatibility

Decoders should reject unknown transform names. Future encoders may improve
planning as long as they keep emitting supported transforms and valid `SLB1`
artifacts.

New transforms, encryption formats, chunking layouts, dictionaries, or semantic
residual formats require a new explicit compatibility contract.
