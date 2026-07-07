# Security

Star Light Codec currently publishes an exact compression artifact format, not a
production encryption system.

## Supported Security Boundary

The reference decoder is expected to fail closed on malformed `SLB1` artifacts:

- bad magic;
- truncated header;
- length mismatch;
- invalid JSON;
- unsupported transform names;
- payload digest mismatch;
- final input digest mismatch.

## Not Yet Supported

- Production encryption.
- Authentication of untrusted artifacts beyond SHA-256 integrity checks inside
  the artifact.
- Protection against malicious decompression bombs beyond the current bounded
  transform depth.
- Formal side-channel analysis.

Future sealed artifacts should be developed under an explicit threat model and
should use well-reviewed cryptographic primitives.
