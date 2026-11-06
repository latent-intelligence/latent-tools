# latent-records-mcp

A standalone, read-only MCP server over a published `latent-records` store, local or in
object storage. It answers latent-records' `records-tools` contract: `describe`, `search`,
`contains`, `get`, and every record as a resource, with nothing of the store package installed.

## Install

```bash
uv tool install "git+https://github.com/latent-intelligence/latent-tools@records-mcp-v0.2.0#subdirectory=records-mcp"
```

Extras, as the store has them: `s3`, `gcs`, `az` for a deployment or a store in a bucket; `text`
(sentence-transformers) and `clip` (open-clip) for the models a store's spaces name. A store
whose space needs a model you did not install says so on the first search.

```bash
uv tool install "latent-records-mcp[s3,text] @ git+https://github.com/latent-intelligence/latent-tools@records-mcp-v0.2.0#subdirectory=records-mcp"
```

To move to a new release, run the line with the new tag; the tool is replaced without asking.

## Run

```bash
latent-records-mcp --store <store>                       # stdio: what a client starts
latent-records-mcp --store <store> --print-config        # the mcpServers block to paste in
latent-records-mcp --deployment <folder>                 # or a deployment's store
claude mcp add records-brindlewick-hives -- latent-records-mcp --store <store> --warm
```

What each takes:

```
--deployment brindlewick-hives/          --store <anywhere>/store/
brindlewick-hives/                       store/
  brindlewick-hives.yaml  name, output:    store.json          the stamp, with store_id
  store/                  <output>/store   records.lance  chunks.lance  …
    store.json  …                          <target>__<space>.lance
                                           media/              figure images
```

`<store>` is a store's own folder, a local path or any URI fsspec opens; it must carry a
stamped `store.json` with a `store_id`, which names everything a server needs. Give it to
serve a store wherever it was copied, with or without the deployment that built it.

`<folder>` is a deployment's folder, holding `<folder name>.yaml`. The server reads the
declaration's `schema_version`, `name` and `output:` and serves `<output>/store`; `output:`
defaults to the folder itself. An older declaration says `root:` for `output:`, and serves
the same. A local declaration's output expands `$VAR` and then `~`, so a variable holding
`~` resolves under home; a remote declaration's output is taken as written; one that needs
this host's environment (`~`, `$VAR`) or its disk (an absolute path) is refused. It reads
nothing else, so it serves some declarations `latent-records serve` refuses, such as one without
`store_id`. `--warm` loads every space's model in the background at start.

HTTP: `--transport http` serves Streamable HTTP at `/mcp` and refuses to start without a
bearer token in `$LATENT_RECORDS_SERVE_TOKEN`, unless `--insecure-local` is given with a
loopback host: exactly `127.0.0.1`, `localhost` or `::1` (or `[::1]`).

`--print-config` prints the block after opening the store, so it is one that starts. When
`output:` names environment variables, the stdio block lists them in `env` as `${VAR}`, never
their values; the client's environment must hold them.

## Without internet access

A space's model downloads on its first search. On a machine with no internet access, unpack
the models beforehand (latent-records' `just models` fills a folder with exactly what a
deployment's spaces load) and set two variables where the server starts:

```bash
HF_HUB_CACHE=<the unpacked models folder>
HF_HUB_OFFLINE=1
```

With both set, `--print-config` names them in the stdio block's `env`, as `${VAR}`. The
latent-records guide `docs/guides/use-a-store-offline.md` covers the client side.

## What it is made of

`serve.py`, `search.py`, `schema.py`, `formats.py`, `store_json.py`, `errors.py`,
`backends/` and `embedders/` are verbatim copies of latent-records', each naming the source
commit in its first line; `store.py` (the read half), `deployment.py` and `cli.py` are adapted
from it. The copy is synced from the store, never edited here; `CHANGELOG.md` names the
commit each release carries. The tool contract and the store format are latent-records'
(`docs/contracts/records-tools.md` and `store.md` there). The fixture under
`tests/fixtures/deployments/` is a small store built from latent-records' own fixture
deployment with its test embedder, by `scripts/fixture_store.py`.

This server and `latent-records serve` give the same tool answers; the workspace's `just conform`
checks that with both installed.
