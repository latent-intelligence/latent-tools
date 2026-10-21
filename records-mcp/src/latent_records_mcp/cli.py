"""`latent-records-mcp` — one store over MCP, read-only, stdio or HTTP, for agents.

The only module that prints. It is `latent-records serve` as a command of its own: the
same options, the same refusals and the same token variable, so a client configured for
one runs the other. Nothing but the protocol reaches stdout on stdio; diagnostics go
through logging, to stderr.

Adapted from latent-records' `cli/serve.py` (b7ca93c).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from typing import NoReturn

import typer
from rich.console import Console
from rich.markup import escape

from . import __version__
from . import serve as serving
from .deployment import load_deployment_folder
from .errors import LatentRecordsError
from .store import Store

TOKEN_ENV = "LATENT_RECORDS_SERVE_TOKEN"

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
    """Serve one deployment's store over MCP, read-only: describe, search, contains,
    get, and each record as a resource.

    One server, one store. HTTP needs a bearer token in $LATENT_RECORDS_SERVE_TOKEN.
    """
    logging.basicConfig(format="%(message)s")
    logging.getLogger("latent_records_mcp").setLevel(logging.INFO)
    if transport not in ("stdio", "http"):
        fail(LatentRecordsError(f"--transport is stdio or http, not {transport!r}"))
    if not deployment:
        fail(
            LatentRecordsError(
                "give the deployment's folder: --deployment <folder>, a local path or "
                "any fsspec URI (s3://…)"
            )
        )
    token = os.environ.get(TOKEN_ENV) or None
    if transport == "http" and (
        problem := serving.refuse_http(token, TOKEN_ENV, host, insecure_local)
    ):
        fail(LatentRecordsError(problem))
    try:
        found = load_deployment_folder(deployment)
        try:
            opened = Store.open(found.store_dir)
        # A remote store fails in whatever its client raises.
        except Exception as exc:
            fail(LatentRecordsError(str(exc) or type(exc).__name__))
        server = serving.build_server(opened)
    except (LatentRecordsError, OSError, ImportError) as exc:
        fail(exc)
    if print_config:
        argv = [*_launcher(), "--deployment", str(found.path.parent)]
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


if __name__ == "__main__":
    app()
