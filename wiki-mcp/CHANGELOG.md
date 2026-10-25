# latent-wiki-mcp

One line per release; the annotated tag `wiki-mcp-v<version>` carries the same line.

- 0.1.1 (2026-10-25) — the reader resynced from latent-wiki 28db78d: concurrent searches of one store no longer share a connection unguarded, a `links` value that is not a list is ignored, raw text in the context store is read only once its stamp is checked, and a `name:` that differs from the deployment's folder is refused.
- 0.1.0 (2026-10-21) — a standalone, read-only MCP server over a published latent-wiki store, local or `s3://`; the reader copied from latent-wiki 991549f.
