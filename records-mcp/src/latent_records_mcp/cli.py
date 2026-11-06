"""`latent-records-mcp` — one store over MCP, read-only, stdio or HTTP, for agents.

The only module that prints. It is `latent-records serve` as a command of its own: the
same options and the same token variable, so a client configured for one runs the other,
and the same tool answers. It reads less of the declaration than the store does, so it
serves some declarations the store refuses; `deployment.py` says what it reads and
refuses. Nothing but the protocol reaches stdout on stdio; diagnostics go
through logging, to stderr.

Adapted from latent-records' `cli/serve.py` (fe6e28c).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console
from rich.markup import escape

from . import __version__
from . import serve as serving
from .deployment import NAME, load_deployment_folder, require_filesystem, root_variables
from .errors import KbStoreError
from .store import Store, as_store_path

TOKEN_ENV = "LATENT_RECORDS_SERVE_TOKEN"

#: What a server started by its client needs to load models with no internet access:
#: named in the client's `env` when set here, never their values.
OFFLINE = ("HF_HUB_CACHE", "HF_HUB_OFFLINE")

err_console = Console(stderr=True)

app = typer.Typer(
    name="latent-records-mcp", add_completion=False, pretty_exceptions_show_locals=False
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"latent-records-mcp {__version__}")
        raise typer.Exit()


def fail(exc: Exception, code: int = 2) -> NoReturn:
    err_console.print(f"[bold red]error[/] {escape(str(exc))}", soft_wrap=True)
    raise typer.Exit(code) from exc


@app.command()
def main(
    deployment: str = typer.Option(
        None,
        "--deployment",
        help="A deployment's folder, holding <folder name>.yaml — a local path or any "
        "fsspec URI (s3://…): serves its store.",
    ),
    store: str = typer.Option(
        None,
        "--store",
        help="A store's folder, a local path or any fsspec URI, served alone: its "
        "store.json names everything a server needs.",
    ),
    warm: bool = typer.Option(
        False, "--warm", help="Load every space's model in the background."
    ),
    transport: str = typer.Option("stdio", "--transport", help="stdio or http."),
    host: str = typer.Option("127.0.0.1", "--host", help="HTTP host."),
    port: int = typer.Option(8765, "--port", help="HTTP port."),
    insecure_local: bool = typer.Option(
        False,
        "--insecure-local",
        help="Allow HTTP without a token, on a loopback host only, for a local test.",
    ),
    print_config: bool = typer.Option(
        False,
        "--print-config",
        help="Print the client's mcpServers block for this store, and exit.",
    ),
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version,
        is_eager=True,
        help="Print the version and exit.",
    ),
) -> None:
    """Serve one store over MCP, read-only: describe, search, contains, get, and each
    record as a resource. Give a deployment's folder, or the store's own.

    One server, one store. HTTP needs a bearer token in $LATENT_RECORDS_SERVE_TOKEN.
    """
    logging.basicConfig(format="%(message)s")
    logging.getLogger("latent_records_mcp").setLevel(logging.INFO)
    if transport not in ("stdio", "http"):
        fail(KbStoreError(f"--transport is stdio or http, not {transport!r}"))
    if bool(deployment) == bool(store):
        fail(
            KbStoreError(
                "give the deployment's folder (--deployment <folder>) or the store's "
                "(--store <folder>): one, a local path or any fsspec URI (s3://…)"
            )
        )
    token = os.environ.get(TOKEN_ENV) or None
    if transport == "http":
        host = serving.bare_host(host)
    if transport == "http" and (
        problem := serving.refuse_http(token, TOKEN_ENV, host, insecure_local)
    ):
        fail(KbStoreError(problem))
    try:
        found = load_deployment_folder(deployment) if deployment else None
        if store and NAME.match(store):
            raise KbStoreError(
                f"{store!r} is a name; give the store's folder, a path or a URI "
                f"(./{store} if it is here)"
            )
        if store and "://" in store:
            require_filesystem(store)
        where = found.store_dir if found else as_store_path(store)
        try:
            opened = Store.open(where)
        # A remote store fails in whatever its client raises.
        except Exception as exc:
            fail(KbStoreError(str(exc) or type(exc).__name__))
        server = serving.build_server(opened)
    except (KbStoreError, OSError, ImportError) as exc:
        fail(exc)
    if print_config:
        named = (
            ["--deployment", _as_given(deployment, found.path.parent)]
            if found
            else ["--store", _as_given(store, where)]
        )
        argv = [*_launcher(), *named]
        if _in_uv_cache(argv[0]):
            err_console.print(
                f"[yellow]warning[/] {escape(UV_CACHE_WARNING)}", soft_wrap=True
            )
        if warm:
            argv.append("--warm")
        block = serving.client_config(
            opened,
            argv=argv,
            transport=transport,
            host=host,
            port=port,
            token_env=TOKEN_ENV,
            token=token is not None,
            variables=[
                *(root_variables(found) if found else []),
                *(v for v in OFFLINE if os.environ.get(v)),
            ],
        )
        typer.echo(json.dumps(block, indent=2))
        return
    if warm:
        serving.warm(opened)
    err_console.print(
        f"[dim]serving[/] {opened.store_id}, read-only, over {transport}",
        highlight=False,
    )
    asyncio.run(
        serving.run(server, transport=transport, host=host, port=port, token=token)
    )


def _launcher() -> list[str]:
    """How a client starts this same command: the console script when that is what
    ran, the interpreter and module when it was `python -m`."""
    script = shutil.which(sys.argv[0]) or os.path.abspath(sys.argv[0])
    if script.endswith(".py") or not os.access(script, os.X_OK):
        return [sys.executable, "-m", "latent_records_mcp.cli"]
    return [os.path.abspath(script)]


UV_CACHE_WARNING = (
    "this command runs from uv's cache, as uvx runs it, and the block names that path: "
    "`uv cache clean` or `uv cache prune` removes it. Install the tool with `uv tool "
    "install` (README) and print the config again."
)


def _in_uv_cache(command: str) -> bool:
    """Whether a command runs from uv's cache, where `uvx` puts a tool it runs."""
    return any(part.startswith("archive-v") for part in Path(command).parts)


if __name__ == "__main__":
    app()


def _as_given(argument: str, resolved: object) -> str:
    """A URI as the operator typed it, a path made absolute: a `UPath` prints a
    Windows ``file:///C:/…`` with one slash fewer, which is another host's path."""
    return argument if "://" in argument else str(resolved)
