# CLAUDE.md — latent-tools

A uv workspace of installable tools, one member per tool, each with its own version and tag.
The root is virtual and is never installed; a colleague installs one member from the git URL with
`#subdirectory=<member>`. The README has the line.

## Boundaries

- **A member stands alone.** It installs with nothing else from this repository or from a store
  package. A server member copies the read path of the store it serves, verbatim where it can,
  with the source file and commit named in each copied file's header. It never imports the store.
- **The contract stays with the store.** A server answers the tool contract its store publishes
  (`latent-wiki`'s `wiki-tools`), and adds nothing to it. A member's README names the contract and
  its version; the fixtures are copies of the store's published-format fixtures.
- **Same answers as the store's own `serve`,** held by `just conform` and by a byte-identity check of
  the copied files against the store's checkout when `LATENT_WIKI_SRC` points at one.
- **Servers are read-only,** stdio first, HTTP behind a bearer token, a deployment folder (local or
  any URI `UPath` opens) as the one argument. Libraries log; only `cli.py` prints.
- **No client anywhere:** fixtures use invented names, a test greps `src/` for them, and nothing
  here names a deployment, a bucket or an engagement.

## Layout

```
pyproject.toml      [tool.uv.workspace] members, one per line; ruff, mypy, pytest config for all
uv.lock             one lockfile, for development only: `uv tool install` reads a member's metadata
Justfile            install · lock · check · test · lint · format · format-check · types · conform · smoke
tests/              workspace checks: every member pins mcp identically; conform
scripts/smoke.py    the clean-environment install
<member>/           pyproject.toml, README.md, CHANGELOG.md, src/<import name>/, tests/
```

## Conventions

uv, ruff, mypy strict, pytest, hatchling, Python 3.11+. One `mcp` pin, `mcp>=2.0,<3`, in every
member. Branches, never `main`; one-line commits `[type]: summary`; gitleaks pre-commit hooks.
A release is one commit bumping the member's version and its `CHANGELOG.md`, then an annotated tag
`<member>-v<version>`.
