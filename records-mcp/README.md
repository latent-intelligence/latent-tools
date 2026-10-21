# latent-records-mcp

A standalone, read-only MCP server over a published `latent-records` store, local or in
object storage. It answers latent-records' `records-tools` contract: `describe`, `search`,
`contains`, `get`, and every record as a resource, with nothing of the store package installed.

## Install

```bash
uv tool install "git+https://github.com/latent-intelligence/latent-tools@records-mcp-v0.1.0#subdirectory=records-mcp"
```

Extras, as the store has them: `s3`, `gcs`, `az` for a deployment in a bucket; `text`
(sentence-transformers) and `clip` (open-clip) for the models a store's spaces name. A store
whose space needs a model you did not install says so on the first search.

```bash
uv tool install "latent-records-mcp[s3,text] @ git+https://github.com/latent-intelligence/latent-tools@records-mcp-v0.1.0#subdirectory=records-mcp"
```

While the repository is private, run `gh auth login` once first. To move to a new release, run
the line with the new tag; the tool is replaced without asking.

## Run

```bash
latent-records-mcp --deployment <folder>                 # stdio: what a client starts
latent-records-mcp --deployment <folder> --print-config  # the mcpServers block to paste in
claude mcp add hives -- latent-records-mcp --deployment <folder> --warm
```

`<folder>` is a deployment's folder, holding `<folder name>.yaml`: a local path, or any URI
fsspec opens. The server reads the declaration's `schema_version`, `name` and `root:` and
serves `<root>/store`, which must carry a stamped `store.json` with a `store_id`; a remote
declaration's root is taken as written; one that needs this host's environment (`~`, `$VAR`)
or its disk (an absolute path) is refused. `--warm` loads
every space's model in the background at start.

HTTP: `--transport http` serves Streamable HTTP at `/mcp` and refuses to start without a
bearer token in `$LATENT_RECORDS_SERVE_TOKEN`, unless `--insecure-local` is given with a
loopback host.

## What it is made of

`serve.py`, `search.py`, `schema.py`, `formats.py`, `store_json.py`, `errors.py`,
`backends/` and `embedders/` are verbatim copies of latent-records', each naming the source
commit in its first line; `store.py` (the read half), `deployment.py` and `cli.py` are adapted
from it. The copy is synced from the store, never edited here; `CHANGELOG.md` names the
commit each release carries. The tool contract and the store format are latent-records'
(`docs/formats/records-tools.md` and `store.md` there). The fixture under
`tests/fixtures/deployments/` is a small store built from latent-records' own fixture
deployment with its test embedder, by `scripts/fixture_store.py`.

This server and `latent-records serve` give the same answers; the workspace's `just conform`
checks that with both installed.
