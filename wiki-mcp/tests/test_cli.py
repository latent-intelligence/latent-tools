"""`latent-wiki-mcp`: a folder in, a server out, over stdio or HTTP with a token."""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from latent_wiki_mcp import __version__
from latent_wiki_mcp.cli import _launcher, app
from latent_wiki_mcp.reader import TOOLS, WikiReader
from typer.testing import CliRunner

pytestmark = pytest.mark.anyio


def _run(*args: str) -> Any:
    return CliRunner().invoke(app, list(args))


def _one_line_refusal(result: Any, says: str) -> None:
    assert result.exit_code == 2, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1, result.output
    assert says in lines[0], result.output


def _declare(folder: str, text: str) -> None:
    from upath import UPath

    (UPath(folder) / "survey.yaml").write_text(text)


@pytest.fixture(autouse=True)
def no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LATENT_WIKI_SERVE_TOKEN", raising=False)


@pytest.fixture
def pushed(deployment: Path, tmp_path: Path) -> Iterator[str]:
    """The deployment copied to an in-memory bucket: a folder that is a URI."""
    import fsspec

    fs = fsspec.filesystem("memory")
    folder = f"memory://{tmp_path.name}/deployments/survey"
    fs.put(str(deployment), folder, recursive=True)
    yield folder
    fs.rm(tmp_path.name, recursive=True)


def _folder(form: str, deployment: Path, pushed: str) -> str:
    return {"path": str(deployment), "file": deployment.as_uri(), "uri": pushed}[form]


def test_version() -> None:
    result = _run("--version")
    assert result.exit_code == 0
    assert result.output.strip() == f"latent-wiki-mcp {__version__}"


def test_needs_a_deployment() -> None:
    _one_line_refusal(_run(), "give the deployment's folder")


@pytest.mark.parametrize("option", ["--store", "--context", "--remote"])
def test_takes_no_store_context_or_remote(option: str, deployment: Path) -> None:
    result = _run(option, str(deployment))
    assert result.exit_code == 2
    assert "No such option" in result.output


def test_an_unknown_transport_is_refused(deployment: Path) -> None:
    _one_line_refusal(
        _run("--deployment", str(deployment), "--transport", "grpc"), "stdio or http"
    )


def test_http_without_a_token_is_refused_before_loading(tmp_path: Path) -> None:
    """The token is checked first, so a missing one is not hidden by a store error."""
    result = _run(
        "--deployment",
        str(tmp_path / "gone"),
        "--transport",
        "http",
        "--host",
        "0.0.0.0",
    )
    _one_line_refusal(result, "LATENT_WIKI_SERVE_TOKEN")
    result = _run(
        "--deployment", str(tmp_path / "gone"), "--transport", "http", "--print-config"
    )
    _one_line_refusal(result, "LATENT_WIKI_SERVE_TOKEN")


def test_insecure_local_is_for_a_loopback_host_only(deployment: Path) -> None:
    result = _run(
        "--deployment", str(deployment), "--transport", "http",
        "--host", "0.0.0.0", "--insecure-local", "--print-config",
    )  # fmt: skip
    _one_line_refusal(result, "LATENT_WIKI_SERVE_TOKEN")


def test_an_unpublished_store_is_refused(deployment: Path) -> None:
    (deployment / "wiki" / "manifest.json").unlink()
    _one_line_refusal(_run("--deployment", str(deployment)), "latent-wiki publish")


def test_the_launcher_is_absolute_wherever_it_was_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "bin").mkdir()
    script = tmp_path / "bin" / "latent-wiki-mcp"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", [os.path.join("bin", "latent-wiki-mcp")])
    [command] = _launcher()
    assert Path(command) == script
    monkeypatch.setattr("sys.argv", ["cli.py"])
    assert _launcher() == [sys.executable, "-m", "latent_wiki_mcp.cli"]


# -- --print-config: a block that starts -------------------------------------


def test_print_config_names_the_store_and_the_folder(deployment: Path) -> None:
    result = _run("--deployment", str(deployment), "--print-config")
    assert result.exit_code == 0, result.output
    assert "warning" not in result.output  # the context resolved and is readable
    entry = json.loads(result.output)["mcpServers"]["wiki-heronsgate"]
    assert entry["args"][-2:] == ["--deployment", str(deployment)]
    assert Path(entry["command"]).is_absolute()


def test_print_config_prints_a_relative_folder_absolute(
    deployment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(deployment.parent)
    result = _run("--deployment", "./survey", "--index-body", "--print-config")
    assert result.exit_code == 0, result.output
    entry = json.loads(result.output)["mcpServers"]["wiki-heronsgate"]
    assert entry["args"][-3:] == ["--deployment", str(deployment), "--index-body"]


def test_print_config_over_http_names_the_token_not_its_value(
    deployment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LATENT_WIKI_SERVE_TOKEN", "s3cret")
    result = _run(
        "--deployment", str(deployment), "--transport", "http", "--print-config"
    )
    assert result.exit_code == 0, result.output
    assert "s3cret" not in result.output
    entry = json.loads(result.output)["mcpServers"]["wiki-heronsgate"]
    assert entry == {
        "type": "http",
        "url": "http://127.0.0.1:8765/mcp",
        "headers": {"Authorization": "Bearer ${LATENT_WIKI_SERVE_TOKEN}"},
    }


def test_print_config_over_http_dials_ipv6_and_omits_an_absent_token(
    deployment: Path,
) -> None:
    result = _run(
        "--deployment", str(deployment), "--transport", "http",
        "--host", "::1", "--insecure-local", "--print-config",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    entry = json.loads(result.output)["mcpServers"]["wiki-heronsgate"]
    assert entry == {"type": "http", "url": "http://[::1]:8765/mcp"}


# -- --deployment <folder>: a local path, file:// or any fsspec URI ----------


@pytest.mark.parametrize("form", ["path", "file", "uri"])
def test_serves_a_deployment_s_folder(
    form: str, deployment: Path, pushed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _folder(form, deployment, pushed)
    if form == "uri":
        shutil.rmtree(deployment)  # nothing local is read
    seen: dict[str, Any] = {}

    async def fake_run(reader: WikiReader, **_: Any) -> None:
        seen["reader"] = reader

    monkeypatch.setattr("latent_wiki_mcp.serve.run", fake_run)
    result = _run("--deployment", folder)
    assert result.exit_code == 0, result.output
    reader = seen["reader"]
    assert reader.id == "heronsgate"
    assert str(reader.root) == f"{folder}/wiki"
    assert reader._check_context() == ""
    assert reader.search("limpet")
    result = _run("--deployment", folder, "--print-config")
    entry = json.loads(result.output)["mcpServers"]["wiki-heronsgate"]
    assert entry["args"][-2:] == ["--deployment", folder]


def test_root_resolves_against_the_folder_wherever_it_is(pushed: str) -> None:
    from latent_wiki_mcp.deployment import load_deployment_folder

    _declare(pushed, "schema_version: 1\nroot: products\nwiki: {config: wiki.yaml}\n")
    found = load_deployment_folder(pushed)
    assert str(found.wiki_dir) == f"{pushed}/products/wiki"
    assert str(found.context_dir) == f"{pushed}/products/context"
    assert found.is_remote


def test_a_uri_folder_is_never_collapsed_to_a_local_path(deployment: Path) -> None:
    import fsspec
    from latent_wiki_mcp.deployment import load_deployment_folder

    fs = fsspec.filesystem("memory")
    fs.put(str(deployment), "memory://survey", recursive=True)
    try:
        found = load_deployment_folder("memory://survey")
    finally:
        fs.rm("/survey", recursive=True)
    assert str(found.wiki_dir) == "memory://survey/wiki"


def test_reads_only_the_version_and_the_root(pushed: str) -> None:
    """Inputs that exist nowhere, and keys this server has no use for, do not stop a
    server that never reads them: the authoring side owns them."""
    _declare(
        pushed,
        "schema_version: 1\nwiki: {config: wiki.yaml}\n"
        "inputs:\n  docs: {format: doc-intelligence, path: /no/such/input, "
        "artifacts: [text]}\nfuture_key: whatever\n",
    )
    result = _run("--deployment", pushed, "--print-config")
    assert result.exit_code == 0, result.output


def test_malformed_yaml_is_one_line(deployment: Path) -> None:
    _declare(str(deployment), "schema_version: 1\nroot: [unclosed\n")
    _one_line_refusal(_run("--deployment", str(deployment)), "not valid YAML")


def test_a_case_variant_of_root_is_refused(deployment: Path) -> None:
    _declare(str(deployment), "schema_version: 1\nRoot: elsewhere\n")
    _one_line_refusal(_run("--deployment", str(deployment)), "did you mean root")


def test_a_remote_root_is_never_expanded_with_this_environment(
    pushed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`$VAR` in a remote declaration is refused as local notation, and never carries
    this process's environment into a request path."""
    monkeypatch.setenv("LATENT_WIKI_SERVE_TOKEN", "sekrit")
    for root in ("x-${LATENT_WIKI_SERVE_TOKEN}", "$HOME/wikis", "~/wikis"):
        _declare(pushed, f"schema_version: 1\nroot: {root}\n")
        result = _run("--deployment", pushed)
        _one_line_refusal(result, "is a local path")
        assert "sekrit" not in result.output


def test_an_unknown_schema_version_is_refused(deployment: Path) -> None:
    _declare(str(deployment), "schema_version: 2\nwiki: {config: wiki.yaml}\n")
    _one_line_refusal(_run("--deployment", str(deployment)), "schema_version 2")


def test_a_local_folder_may_keep_its_root_on_local_disk(
    deployment: Path, tmp_path: Path
) -> None:
    from latent_wiki_mcp.deployment import load_deployment_folder

    moved = tmp_path / "elsewhere"
    shutil.move(str(deployment / "wiki"), moved / "wiki")
    _declare(
        deployment.as_uri(),
        f"schema_version: 1\nroot: {moved}\nwiki: {{config: wiki.yaml}}\n",
    )
    found = load_deployment_folder(deployment.as_uri())
    assert str(found.wiki_dir) == str(moved / "wiki")


def test_a_name_is_refused(deployment: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(deployment.parent)
    _one_line_refusal(_run("--deployment", "survey"), "give the deployment's folder")


def test_a_folder_without_a_declaration_is_refused(tmp_path: Path, pushed: str) -> None:
    (tmp_path / "empty").mkdir()
    for folder in (str(tmp_path / "empty"), pushed.replace("survey", "absent")):
        _one_line_refusal(_run("--deployment", folder), "no deployment declaration")


def test_a_declaration_file_is_refused_for_its_folder(deployment: Path) -> None:
    result = _run("--deployment", str(deployment / "survey.yaml"))
    _one_line_refusal(result, "give the folder that holds it")


def test_a_folder_with_no_name_is_refused() -> None:
    for folder in ("memory://", "s3://bucket"):
        _one_line_refusal(_run("--deployment", folder), "has no name")


def test_a_failed_read_says_why(pushed: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from fsspec.implementations.memory import MemoryFileSystem

    def denied(*_: Any, **__: Any) -> Any:
        raise RuntimeError("Unable to locate credentials")

    monkeypatch.setattr(MemoryFileSystem, "_open", denied)
    result = _run("--deployment", pushed)
    _one_line_refusal(result, "Unable to locate credentials")
    assert "no deployment" not in result.output


def test_a_remote_declaration_with_a_local_root_is_refused(pushed: str) -> None:
    for root in (
        "/srv/wikis/survey",
        "~/wikis/survey",
        "file:///srv/wikis/survey",
        "simplecache::file:///srv/wikis/survey",
    ):
        _declare(
            pushed, f"schema_version: 1\nroot: {root}\nwiki: {{config: wiki.yaml}}\n"
        )
        _one_line_refusal(_run("--deployment", pushed), "is a local path")


def test_a_scheme_whose_extra_is_missing_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fsspec

    def missing(protocol: str) -> Any:
        raise ImportError(f"Install the filesystem for {protocol}")

    monkeypatch.setattr(fsspec, "get_filesystem_class", missing)
    result = _run("--deployment", "s3://bucket/deployments/survey")
    _one_line_refusal(result, "latent-wiki-mcp[s3]")


def test_a_scheme_fsspec_does_not_know_is_refused(deployment: Path) -> None:
    _one_line_refusal(
        _run("--deployment", "nosuchscheme://x/deployments/survey"), "not a scheme"
    )
    _declare(
        str(deployment),
        "schema_version: 1\nroot: nosuchscheme://x/y\nwiki: {config: wiki.yaml}\n",
    )
    _one_line_refusal(_run("--deployment", str(deployment)), "not a scheme")


def test_a_chained_url_is_read_by_its_last_scheme() -> None:
    from latent_wiki_mcp.deployment import _scheme, require_filesystem

    assert _scheme("zip://pages::s3://b/a.zip") == "s3"
    require_filesystem("simplecache::memory://x/y")


# -- over stdio, as a client starts it ---------------------------------------


async def test_stdout_carries_only_the_protocol(deployment: Path) -> None:
    """The real thing: the module started as a subprocess, a handshake, a tool call."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "latent_wiki_mcp.cli", "--deployment", str(deployment)],
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        init = await session.initialize()
        assert init.server_info.name == "heronsgate"
        tools = await session.list_tools()
        assert [t.name for t in tools.tools] == [name for name, _, _ in TOOLS]
        result = await session.call_tool("wiki_search", {"query": "limpets"})
    assert "heronsgate:count-limpets" in result.content[0].text  # type: ignore[union-attr]
