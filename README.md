# latent-tools

Installable tooling from Latent Intelligence: a uv workspace, one member per tool. Each member
installs on its own, with nothing else from this repository:

```bash
uv tool install "git+https://github.com/latent-intelligence/latent-tools@<member>-v<version>#subdirectory=<member>"
```

| Member | Command | What it is |
|---|---|---|
| [`wiki-mcp`](wiki-mcp/) | `latent-wiki-mcp` | A read-only MCP server over a published `latent-wiki` store, local or `s3://` |

**Status: an experiment.** The stores' own `serve` commands (`latent-wiki serve`) remain the
documented way to serve a store. These servers answer the same tool contracts and are kept giving
the same answers; the experiment decides whether serving moves here.

While the repository is private, `gh auth login` once, so git can authenticate; the install line
is the same afterwards. To move to a new release, run the line with the new tag: `uv tool upgrade`
holds a pin. Running the line with a different tag replaces the installed version without asking.

## Developing

```bash
uv sync --all-packages        # every member and the dev tools, one lockfile
just check                    # ruff, mypy strict, pytest, for every member
just check wiki-mcp           # one member
just smoke wiki-mcp           # install the member into a clean uv tool environment and talk to it over stdio
just conform                  # a member and the store's own serve give the same answers (needs both installed)
```

A new tool is a new directory and one line in the root `pyproject.toml`. A server member is named
`<store>-mcp`; another tool is named by what it does. Each member has its own version, tagged
`<member>-v<version>`.
