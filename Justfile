# latent-tools — task runner. `just` lists the recipes.
#
# Convenience only: install, check, and the two workspace checks (conform, smoke). Every recipe
# takes an optional member (`just check wiki-mcp`); with none it runs the whole workspace.
# No deployments live here, so there are no data recipes. Reads no variables.

default:
    @just --list

# Install every member and the dev tools.
install:
    uv sync --all-packages --all-extras

# Update the lockfile.
lock:
    uv lock

# Everything CI runs.
check member="": (lint member) (format-check member) (types member) (test member)

# The tests, for one member or all.
test member="":
    uv run pytest -q {{member}}

# ruff check.
lint member="":
    uv run ruff check {{ if member == "" { "." } else { member } }}

# ruff format.
format member="":
    uv run ruff format {{ if member == "" { "." } else { member } }}

# ruff format --check.
format-check member="":
    uv run ruff format --check {{ if member == "" { "." } else { member } }}

# mypy strict.
types member="":
    uv run mypy {{ if member == "" { "" } else { member + "/src" } }}

# -- latent-tools -----------------------------------------------------------

# Both patterns give the same answers: a member and the store's own serve, over stdio, on the same fixtures.
conform:
    uv run pytest -q -m conform tests/test_conform.py

# Clean-environment install of one member from this checkout, then a stdio handshake.
smoke member:
    uv run python scripts/smoke.py {{member}}
