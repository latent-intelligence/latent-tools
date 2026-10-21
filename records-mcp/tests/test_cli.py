"""`latent-records-mcp`: a folder in, a server out, over stdio or HTTP with a token."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from latent_records_mcp import __version__
from latent_records_mcp.cli import TOKEN_ENV, _launcher, app
from typer.testing import CliRunner

from .conftest import STORE_ID

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

    (UPath(folder) / f"{folder.rstrip('/').rsplit('/', 1)[-1]}.yaml").write_text(text)


@pytest.fixture(autouse=True)
def no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)


@pytest.fixture
def pushed(folder: Path, tmp_path: Path) -> Iterator[str]:
    """A folder that is a URI, as `s3://…/deployments/<name>` is. LanceDB cannot open
    `memory://`, so this holds a declaration only, for what is decided before a store
    is opened."""
    import fsspec

    fs = fsspec.filesystem("memory")
    uri = f"memory://{tmp_path.name}/deployments/{folder.name}"
    fs.put(str(folder / f"{folder.name}.yaml"), f"{uri}/{folder.name}.yaml")
    yield uri
    fs.rm(tmp_path.name, recursive=True)


def _folder(form: str, folder: Path) -> str:
    return {"path": str(folder), "file": folder.as_uri()}[form]


def test_version() -> None:
    result = _run("--version")
    assert result.exit_code == 0
    assert result.output.strip() == f"latent-records-mcp {__version__}"


def test_needs_a_deployment() -> None:
    _one_line_refusal(_run(), "give the deployment's folder")


@pytest.mark.parametrize("option", ["--store", "--remote"])
def test_takes_no_store_or_remote(option: str, folder: Path) -> None:
    result = _run(option, str(folder))
    assert result.exit_code == 2 and "No such option" in result.output


def test_an_unknown_transport_is_refused(folder: Path) -> None:
    _one_line_refusal(
        _run("--deployment", str(folder), "--transport", "sse"), "stdio or http"
    )


def test_http_without_a_token_is_refused_before_loading(tmp_path: Path) -> None:
    for extra in ([], ["--print-config"]):
        result = _run(
            "--deployment", str(tmp_path / "gone"), "--transport", "http",
            "--host", "0.0.0.0", *extra,
        )  # fmt: skip
        _one_line_refusal(result, TOKEN_ENV)


def test_insecure_local_is_for_a_loopback_host_only(folder: Path) -> None:
    result = _run(
        "--deployment", str(folder), "--transport", "http",
        "--host", "0.0.0.0", "--insecure-local", "--print-config",
    )  # fmt: skip
    _one_line_refusal(result, TOKEN_ENV)


def test_a_store_without_an_id_is_refused_at_the_cli(folder: Path) -> None:
    stamp = folder / "store" / "store.json"
    data = json.loads(stamp.read_text())
    del data["store_id"]
    stamp.write_text(json.dumps(data))
    _one_line_refusal(_run("--deployment", str(folder), "--print-config"), "store_id")


def test_an_unstamped_store_is_refused(folder: Path) -> None:
    (folder / "store" / "store.json").unlink()
    _one_line_refusal(_run("--deployment", str(folder)), "not a latent-records store")


def test_the_launcher_is_absolute_wherever_it_was_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "bin").mkdir()
    script = tmp_path / "bin" / "latent-records-mcp"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", [os.path.join("bin", "latent-records-mcp")])
    assert _launcher() == [str(script)]
    monkeypatch.setattr("sys.argv", ["cli.py"])
    assert _launcher() == [sys.executable, "-m", "latent_records_mcp.cli"]


# -- --print-config: a block that starts -------------------------------------


def test_print_config_over_http_names_the_token_not_its_value(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_ENV, "s3cret")
    result = _run("--deployment", str(folder), "--transport", "http", "--print-config")
    assert result.exit_code == 0, result.output
    assert "s3cret" not in result.output
    entry = json.loads(result.output)["mcpServers"][f"records-{STORE_ID}"]
    assert entry == {
        "type": "http",
        "url": "http://127.0.0.1:8765/mcp",
        "headers": {"Authorization": f"Bearer ${{{TOKEN_ENV}}}"},
    }
    result = _run(
        "--deployment", str(folder), "--transport", "http",
        "--insecure-local", "--print-config",
    )  # fmt: skip
    entry = json.loads(result.output)["mcpServers"][f"records-{STORE_ID}"]
    assert entry["headers"] == {"Authorization": f"Bearer ${{{TOKEN_ENV}}}"}


def test_print_config_over_http_dials_ipv6_and_omits_an_absent_token(
    folder: Path,
) -> None:
    result = _run(
        "--deployment", str(folder), "--transport", "http",
        "--host", "::1", "--insecure-local", "--print-config",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    entry = json.loads(result.output)["mcpServers"][f"records-{STORE_ID}"]
    assert entry == {"type": "http", "url": "http://[::1]:8765/mcp"}


async def _starts(entry: dict[str, Any]) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=entry["command"], args=entry["args"])
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        started = await session.initialize()
        assert started.server_info.name == STORE_ID
        tools = [t.name for t in (await session.list_tools()).tools]
        assert tools == ["describe", "search", "contains", "get"]
        result = await session.call_tool("contains", {"phrase": "frame card"})
        assert json.loads(result.content[0].text)["total"] == 4  # type: ignore[union-attr]


@pytest.mark.parametrize("form", ["path", "file"])
async def test_print_config_for_a_folder_is_a_block_that_starts(
    form: str, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The block names the folder, never the store's internal paths, and starts
    over real stdio; `file://` goes through fsspec and LanceDB's own object store as
    `s3://` would."""
    monkeypatch.setattr("sys.argv", ["cli.py"])
    where = _folder(form, folder)
    result = _run("--deployment", where, "--warm", "--print-config")
    assert result.exit_code == 0, result.output
    entry: dict[str, Any] = json.loads(result.output)["mcpServers"][
        f"records-{STORE_ID}"
    ]
    assert Path(entry["command"]).is_absolute()
    assert entry["args"][-3:] == ["--deployment", where, "--warm"]
    await _starts(entry)


def test_print_config_prints_a_relative_folder_absolute(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(folder.parent)
    result = _run("--deployment", f"./{folder.name}", "--print-config")
    assert result.exit_code == 0, result.output
    entry = json.loads(result.output)["mcpServers"][f"records-{STORE_ID}"]
    assert entry["args"][-1] == str(folder)


# -- --deployment <folder>: a local path, file:// or any fsspec URI ----------


def test_reads_only_the_version_name_and_root(folder: Path) -> None:
    """Keys this server has no use for do not stop it: the authoring side owns them."""
    _declare(
        str(folder),
        "schema_version: 1\nstore_id: brindlewick-hives\nspaces: {}\n"
        "inputs:\n  docs: {format: doc-intelligence, path: /no/such, "
        "artifacts: [text]}\nfuture_key: whatever\n",
    )
    result = _run("--deployment", str(folder), "--print-config")
    assert result.exit_code == 0, result.output


def test_a_name_that_differs_from_the_file_is_refused(folder: Path) -> None:
    _declare(str(folder), "schema_version: 1\nname: other-hives\n")
    _one_line_refusal(_run("--deployment", str(folder)), "differs from the file's")


def test_an_unknown_schema_version_is_refused(folder: Path) -> None:
    _declare(str(folder), "schema_version: 2\n")
    _one_line_refusal(_run("--deployment", str(folder)), "schema_version 2")


def test_malformed_yaml_is_one_line(folder: Path) -> None:
    _declare(str(folder), "schema_version: 1\nroot: [unclosed\n")
    result = _run("--deployment", str(folder))
    _one_line_refusal(result, "not valid YAML")
    assert "line 3, column 1" in result.output


def test_a_case_variant_of_root_is_refused(folder: Path) -> None:
    _declare(str(folder), "schema_version: 1\nRoot: elsewhere\n")
    _one_line_refusal(_run("--deployment", str(folder)), "did you mean root")


def test_root_resolves_against_the_folder_wherever_it_is(pushed: str) -> None:
    from latent_records_mcp.deployment import load_deployment_folder

    _declare(pushed, "schema_version: 1\nroot: products\n")
    found = load_deployment_folder(pushed)
    assert str(found.store_dir) == f"{pushed}/products/store"
    assert found.is_remote


def test_a_remote_root_is_never_expanded_with_this_environment(
    pushed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_ENV, "sekrit")
    for root in (f"x-${{{TOKEN_ENV}}}", "$HOME/stores", "~/stores"):
        _declare(pushed, f"schema_version: 1\nroot: {root}\n")
        result = _run("--deployment", pushed)
        _one_line_refusal(result, "names this host's environment")
        assert "sekrit" not in result.output
    _declare(pushed, "schema_version: 1\nroot: /srv/stores\n")
    _one_line_refusal(_run("--deployment", pushed), "is a local path")


def test_a_name_is_refused(folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(folder.parent)
    _one_line_refusal(_run("--deployment", folder.name), "give the deployment's folder")


def test_a_folder_without_a_declaration_is_refused(tmp_path: Path, pushed: str) -> None:
    (tmp_path / "empty").mkdir()
    for where in (str(tmp_path / "empty"), pushed.replace(STORE_ID, "absent")):
        _one_line_refusal(_run("--deployment", where), "no deployment declaration")


def test_a_declaration_file_is_refused_for_its_folder(folder: Path) -> None:
    result = _run("--deployment", str(folder / f"{folder.name}.yaml"))
    _one_line_refusal(result, "give the folder that holds it")


def test_a_folder_with_no_name_is_refused() -> None:
    for where in ("memory://", "file:///"):
        _one_line_refusal(_run("--deployment", where), "has no name")


def test_a_failed_read_says_why(pushed: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from fsspec.implementations.memory import MemoryFileSystem

    def denied(*_: Any, **__: Any) -> Any:
        raise RuntimeError("Unable to locate credentials")

    monkeypatch.setattr(MemoryFileSystem, "_open", denied)
    result = _run("--deployment", pushed)
    _one_line_refusal(result, "Unable to locate credentials")
    assert "no deployment" not in result.output


def test_a_scheme_whose_extra_is_missing_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fsspec

    def missing(protocol: str) -> Any:
        raise ImportError(f"Install the filesystem for {protocol}")

    monkeypatch.setattr(fsspec, "get_filesystem_class", missing)
    result = _run("--deployment", "gs://bucket/deployments/hives")
    _one_line_refusal(result, "latent-records-mcp[gcs]")


def test_a_scheme_fsspec_does_not_know_is_refused(folder: Path) -> None:
    _one_line_refusal(
        _run("--deployment", "nosuchscheme://x/deployments/hives"), "not a scheme"
    )
    _declare(str(folder), "schema_version: 1\nroot: nosuchscheme://x/y\n")
    _one_line_refusal(_run("--deployment", str(folder)), "not a scheme")


def test_a_store_that_cannot_be_opened_says_why(folder: Path) -> None:
    (folder / "store" / "chunks.lance").rename(folder / "store" / "gone.lance")
    _one_line_refusal(_run("--deployment", str(folder)), "chunks")
