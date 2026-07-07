# Licensing Policy

Star Light Codec uses a format-first licensing policy.

The goal is to make the codec format, compatibility profile, schemas, examples,
and test vectors as easy as possible to reimplement in independent projects.
Reference implementation code keeps Apache-2.0 so users also get the explicit
patent grant attached to that code.

| Material | Default license |
| --- | --- |
| Reference implementation code, CLI, tests, benchmark scripts | Apache-2.0 |
| Codec format, compatibility profile, schemas, transport capsule spec | CC0-1.0 |
| Test vectors, fixtures, sample metadata, benchmark result data | CC0-1.0 |
| Narrative documentation, README files, roadmap text | CC BY 4.0 unless marked otherwise |

The root [LICENSE](LICENSE) file contains the Apache-2.0 license for reference
implementation code.

Files that define codec interoperability should include an SPDX marker such as:

```text
SPDX-License-Identifier: CC0-1.0
```

The [LICENSES](LICENSES/) directory contains short SPDX-style pointers for the
non-code licenses used by this repository.

This repository is intended for broad public reuse. If a file needs a different
license, add a short note in that file or in this document.
