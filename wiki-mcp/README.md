# latent-wiki-mcp

A standalone, read-only MCP server over a published `latent-wiki` store, local or `s3://`.
It answers latent-wiki's `wiki-tools` contract: the same nine tools, every page as a resource,
the same footers and `cut:` lines, with nothing of the store package installed.

## Install

```bash
uv tool install "git+https://github.com/latent-intelligence/latent-tools@wiki-mcp-v0.1.1#subdirectory=wiki-mcp"
```

For a deployment in a bucket, add the `s3` extra:

```bash
uv tool install "latent-wiki-mcp[s3] @ git+https://github.com/latent-intelligence/latent-tools@wiki-mcp-v0.1.1#subdirectory=wiki-mcp"
```

To move to a new release, run the line with the new tag; the tool is replaced without asking.

## Run

```bash
latent-wiki-mcp --deployment <folder>                 # stdio: what a client starts
latent-wiki-mcp --deployment <folder> --print-config  # the mcpServers block to paste in
claude mcp add wiki-heronsgate -- latent-wiki-mcp --deployment <folder>
```

Name the server `wiki-<store_id>`, as `--print-config` does. The label is the client's own:
the tools always name a page `<store_id>:<key>`, whatever the label, and a client that shows
its own `<label>:<key>` form swaps the store's id for the label itself.

`<folder>` is a deployment's folder, holding `<folder name>.yaml`: a local path, or any URI
fsspec opens (`s3://…/deployments/<name>`). The server reads the declaration's `schema_version`,
`name` and `root:` and serves `<root>/wiki` with `<root>/context`; a remote declaration's root
is taken as written; one that needs this host's environment (`~`, `$VAR`) or its disk (an
absolute path) is refused. It reads nothing else, so it serves some declarations `latent-wiki serve`
refuses, such as one without `wiki:`. It refuses a name, a `name:` that differs from the
folder's, a store's own `wiki/` folder in place of its deployment's, an unpublished store,
and a remote declaration whose root is a local path.

HTTP: `--transport http` serves Streamable HTTP at `/mcp` and refuses to start without a bearer
token in `$LATENT_WIKI_SERVE_TOKEN`, unless `--insecure-local` is given with a loopback host: exactly `127.0.0.1`, `localhost` or
`::1`.
`--index-body` ranks over page bodies too.

## What it is made of

`reader.py`, `_fts.py`, `views.py`, `formats.py` and `serve.py` are verbatim copies of
latent-wiki's, each naming the source commit in its first line; `manifest.py`, `core.py` and
`errors.py` are trimmed to what the reader imports, each naming its source and commit;
`deployment.py` and `cli.py` are adapted from it. The copy is synced from the store, never edited here; `CHANGELOG.md`
names the commit each release carries. The tool contract, the store format and their
fixtures are latent-wiki's (`docs/contracts/wiki-tools.md`, `wiki-store.md`, `context-store.md`
there); the fixtures under `tests/fixtures/formats/` are copies with invented names.

This server and `latent-wiki serve` give the same tool answers; the workspace's `just conform`
checks that with both installed.
