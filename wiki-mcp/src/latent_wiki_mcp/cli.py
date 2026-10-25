"""`latent-wiki-mcp` — one published wiki store over MCP, read-only, stdio or HTTP.

The only module that prints. It is `latent-wiki serve` as a command of its own: the
same options and the same token variable, so a client configured for one runs the other,
and the same tool answers. It reads less of the declaration than the store does, so it
serves some declarations the store refuses; `deployment.py` says what it reads and
refuses. Nothing but the protocol reaches stdout on stdio; diagnostics go to
stderr.

Adapted from latent-wiki's `cli/serve.py` (28db78d).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from . import __version__
from . import serve as serving
from .deployment import load_deployment_folder
from .errors import LatentWikiError
from .reader import WikiReader

TOKEN_ENV = "LATENT_WIKI_SERVE_TOKEN"

err_console = Console(stderr=True)

app = typer.Typer(
    name="latent-wiki-mcp", add_completion=False, pretty_exceptions_show_locals=False
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"latent-wiki-mcp {__version__}")
        raise typer.Exit()


def _fail(message: str) -> None:
    err_console.print(f"[bold red]error[/] {escape(message)}", soft_wrap=True)
    raise typer.Exit(2)


@app.command()
def main(
    deployment: str = typer.Option(
        None,
        "--deployment",
        help="A deployment's folder, holding <folder name>.yaml — a local path or any "
        "fsspec URI (s3://…): serves its wiki with its context.",
    ),
    index_body: bool = typer.Option(
        False, "--index-body", help="Rank over page bodies too, not only the gist."
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
    """Serve one deployment's published wiki over MCP, read-only.

    One server, one store: the client chooses which to connect to. HTTP needs a bearer
    token in $LATENT_WIKI_SERVE_TOKEN.
    """
    if transport not in ("stdio", "http"):
        _fail(f"--transport is stdio or http, not {transport!r}")
    if not deployment:
        _fail(
            "give the deployment's folder: --deployment <folder>, a local path or any "
            "fsspec URI (s3://…)"
        )
    token = os.environ.get(TOKEN_ENV) or None
    if transport == "http" and (
        problem := _refuse_insecure_host(token, host, insecure_local)
    ):
        _fail(problem)
    if transport == "http" and (
        problem := serving.refuse_http(token, TOKEN_ENV, host, insecure_local)
    ):
        _fail(problem)
    try:
        found = load_deployment_folder(deployment)
        reader = WikiReader(
            str(found.wiki_dir), context=str(found.context_dir), index_body=index_body
        )
    except LatentWikiError as exc:
        _fail(str(exc))
    if print_config:
        if problem := reader._check_context() if reader.context else "":
            err_console.print(f"[yellow]warning[/] {escape(problem)}", soft_wrap=True)
        argv = [*_launcher(), "--deployment", str(found.path.parent)]
        if _in_uv_cache(argv[0]):
            err_console.print(
                f"[yellow]warning[/] {escape(UV_CACHE_WARNING)}", soft_wrap=True
            )
        if reader.index_body:
            argv.append("--index-body")
        block = serving.client_config(
            reader,
            argv=argv,
            transport=transport,
            host=host,
            port=port,
            token_env=TOKEN_ENV,
            token=token is not None,
        )
        typer.echo(json.dumps(block, indent=2))
        return
    err_console.print(
        f"[dim]serving[/] {reader.id} — {len(reader.pages)} pages, read-only, "
        f"over {transport}"
    )
    asyncio.run(
        serving.run(reader, transport=transport, host=host, port=port, token=token)
    )


def _launcher() -> list[str]:
    """How a client should start this same command: the console script when that is
    what ran, the interpreter and module when it was `python -m`."""
    script = shutil.which(sys.argv[0]) or os.path.abspath(sys.argv[0])
    if script.endswith(".py") or not os.access(script, os.X_OK):
        return [sys.executable, "-m", "latent_wiki_mcp.cli"]
    return [os.path.abspath(script)]


UV_CACHE_WARNING = (
    "this command runs from uv's cache, as uvx runs it, and the block names that path: "
    "`uv cache clean` or a new release removes it. Install the tool with `uv tool "
    "install` (README) and print the config again."
)


def _in_uv_cache(command: str) -> bool:
    """Whether a command runs from uv's cache, where `uvx` puts a tool it runs."""
    return any(part.startswith("archive-v") for part in Path(command).parts)


def _refuse_insecure_host(token: str | None, host: str, insecure_local: bool) -> str:
    """Why `--insecure-local` may not serve this host, or "" when it may or a token
    is set. The loopback list is exact, as the store's is: 127.0.0.2 is not on it."""
    if token or not insecure_local or host in serving.LOOPBACK:
        return ""
    return (
        f"{host!r} is not a loopback host: --insecure-local takes exactly "
        f"{', '.join(serving.LOOPBACK)}. Use one of those, or set ${TOKEN_ENV}."
    )


if __name__ == "__main__":
    app()
