# Latent-Tools

Tools for connecting AI assistants to published knowledge and records.
Install only the tool you need.

| Tool | What it does |
|---|---|
| [wiki-mcp](wiki-mcp/) | Read-only access to a published wiki, stored locally or in S3 |
| [records-mcp](records-mcp/) | Read-only access to published records, stored locally or in object storage |

These MCP servers are experimental. The existing `latent-wiki serve` and
`latent-records serve` commands remain the recommended way to serve a store.

## Install

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed:

**Wiki**

```bash
uv tool install "git+https://github.com/latent-intelligence/latent-tools@wiki-mcp-v0.1.2#subdirectory=wiki-mcp"
latent-wiki-mcp --help
```

**Records**

```bash
uv tool install "git+https://github.com/latent-intelligence/latent-tools@records-mcp-v0.1.2#subdirectory=records-mcp"
latent-records-mcp --help
```

Each tool has its own releases. To update, rerun its install command with
the new release tag.

## Shortcut: run with uvx

Use `uvx` to run either tool without a separate install step:

```bash
uvx --from "git+https://github.com/latent-intelligence/latent-tools@wiki-mcp-v0.1.2#subdirectory=wiki-mcp" latent-wiki-mcp --help
```

```bash
uvx --from "git+https://github.com/latent-intelligence/latent-tools@records-mcp-v0.1.2#subdirectory=records-mcp" latent-records-mcp --help
```

`uvx` runs the tool from uv's cache, which `uv cache clean` or `uv cache prune` removes. So
`--print-config` under `uvx` still prints the cache path, and warns on stderr: install the
tool with `uv tool install` before pasting a config into a client.

See each tool’s README for configuration and usage.

## Developing

```bash
uv sync --all-packages
just check                 # check all tools
just check wiki-mcp         # check one tool
just smoke wiki-mcp         # check installation and startup
just conform               # compare with the stores' serve commands
```
