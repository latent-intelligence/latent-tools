# latent-records-mcp

One line per release; the annotated tag `records-mcp-v<version>` carries the same line.

- 0.1.1 (2026-10-25) — the read path resynced from latent-records 5a75235: a local root expands `$VAR` before `~`, so a variable holding `~` resolves under home; the copied files' spec paths now read `docs/contracts/`.
- 0.1.0 (2026-10-21) — a standalone, read-only MCP server over a published latent-records store, local or in object storage; the read path copied from latent-records b7ca93c.
