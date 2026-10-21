"""The deployment folder a server is given, read for serving and for nothing else.

A deployment is a folder holding `<folder name>.yaml`: a local path, or any URI fsspec
opens, such as the copy a store's `just push` writes to a bucket. A server needs two
things from the declaration: `schema_version`, which must be one it knows, and `root:`,
where the products live, resolved against the folder wherever it is. The wiki is
`<root>/wiki` and its context `<root>/context`. It also checks `name`, as the store
does. Everything else in the declaration (inputs, the authored config, briefs) is the
authoring side's and is not read, so a declaration that grows a key still serves; the
one typo it catches is a case-variant of `root`, which would otherwise serve the wrong
folder in silence. A name is refused: looking one up is for the store's own authoring
commands.

A declaration read from another host is taken as written: a root with `~` or `$VAR` is
refused rather than expanded with this host's environment, since no other host's values
could be meant, and a value expanded here could carry this process's secrets into a
request path.

Adapted from latent-wiki's `deployment.py` (991549f): the same refusals, fewer keys.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import fsspec  # type: ignore[import-untyped]
import yaml
from upath import UPath

from .errors import DeploymentError

#: fsspec schemes this package offers an extra for, by the extra's name.
EXTRAS = {"s3": "s3", "s3a": "s3"}

#: Schemes that name the reading host's own disk: a local path spelled as a URI.
LOCAL_SCHEMES = {"file", "local"}

SCHEMA_VERSION = 1

NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class Deployment:
    name: str
    path: Path
    root: Path

    @property
    def context_dir(self) -> Path:
        return self.root / "context"

    @property
    def wiki_dir(self) -> Path:
        return self.root / "wiki"

    @property
    def is_remote(self) -> bool:
        return "://" in str(self.root)


def as_path(value: Any) -> Path:
    """A `UPath` as a `Path`: it offers everything this package uses of one."""
    return cast(Path, value)


def load_deployment_folder(folder: str) -> Deployment:
    """Load the deployment whose folder is `folder`, for serving its wiki.

    A declaration read from another host may not put `root:` on this host's disk:
    no other host could follow it.
    """
    if NAME.match(folder):
        raise DeploymentError(
            f"{folder!r} is a name; give the deployment's folder, a path or a URI "
            f"(./{folder} if it is here)"
        )
    if "://" in folder:
        require_filesystem(folder)
        directory = as_path(UPath(folder))
    else:
        directory = Path(folder).expanduser().resolve()
    if not directory.name:
        raise DeploymentError(
            f"{folder!r} has no name to find its declaration by; give the "
            f"deployment's folder"
        )
    source = directory / f"{directory.name}.yaml"
    try:
        # Read, never `is_file()` first: fsspec answers False for missing
        # credentials or a refused request, which would read as no declaration.
        text = source.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DeploymentError(f"no deployment declaration at {source}") from exc
    except NotADirectoryError as exc:
        raise DeploymentError(
            f"{directory} is a file; give the folder that holds it"
        ) from exc
    except Exception as exc:  # a remote read fails in whatever its client raises
        raise DeploymentError(f"cannot read {source}: {exc}") from exc
    deployment = _parse(text, source)
    if deployment.is_remote:
        require_filesystem(str(deployment.root))
    return deployment


def require_filesystem(uri: str) -> None:
    """Refuse a URI whose scheme this install cannot open, naming what would."""
    scheme = _scheme(uri)
    try:
        fsspec.get_filesystem_class(scheme)
    except ImportError as exc:
        if extra := EXTRAS.get(scheme):
            raise DeploymentError(
                f"reading {scheme}:// needs the {extra} extra: "
                f"install latent-wiki-mcp[{extra}]"
            ) from exc
        raise DeploymentError(
            f"reading {scheme}:// needs a filesystem this install lacks: {exc}"
        ) from exc
    except ValueError as exc:
        raise DeploymentError(f"{scheme}:// is not a scheme fsspec knows") from exc


def _scheme(uri: str) -> str:
    """The scheme that opens a URI: the last one of a chained URL (``a::s3://…``)."""
    return uri.rsplit("::", 1)[-1].split("://", 1)[0]


def _on_this_host(location: str) -> bool:
    """Whether a location is on the reading host's own disk: a path, or ``file://``."""
    return "://" not in location or _scheme(location) in LOCAL_SCHEMES


def _looks_local(value: str) -> bool:
    """Whether a declared location can only mean the writing host's own disk."""
    if "://" in value:
        return _scheme(value) in LOCAL_SCHEMES
    return Path(value).is_absolute()


def _names_environment(value: str) -> bool:
    """Whether a declared location would need this host's environment to resolve."""
    return "://" not in value and (value.startswith("~") or "$" in value)


def resolve_location(value: str, base: Path, *, expand: bool = True) -> Path:
    """A path from the declaration: a URI as given, anything else against the file."""
    expanded = os.path.expandvars(os.path.expanduser(value)) if expand else value
    if "://" in expanded:
        try:
            return as_path(UPath(expanded))
        except ValueError as exc:
            raise DeploymentError(
                f"{value!r}: {_scheme(expanded)}:// is not a scheme fsspec knows"
            ) from exc
    path = Path(expanded)
    return (path if path.is_absolute() else base / path).resolve()


def _yaml_problem(exc: yaml.YAMLError) -> str:
    """One line saying what is wrong and where, when YAML says."""
    if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
        mark = exc.problem_mark
        where = f"line {mark.line + 1}, column {mark.column + 1}"
        return f"{exc.problem or exc.context} at {where}"
    text = str(exc).splitlines()
    return text[0] if text else type(exc).__name__


def _parse(text: str, source: Path) -> Deployment:
    """A declaration's text, read from `source`: its version, name and root."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DeploymentError(
            f"{source}: not valid YAML: {_yaml_problem(exc)}"
        ) from exc
    if not isinstance(raw, dict):
        raise DeploymentError(f"{source}: expected a mapping at the top level")
    version = raw.get("schema_version")
    if version != SCHEMA_VERSION:
        raise DeploymentError(
            f"{source}: schema_version {version!r} is not supported; "
            f"expected {SCHEMA_VERSION}"
        )
    name = str(raw.get("name") or source.stem)
    if not NAME.match(name):
        raise DeploymentError(
            f"{source}: name {name!r} must be lowercase letters, digits and hyphens"
        )
    for key in raw:
        if str(key).lower() == "root" and key != "root":
            raise DeploymentError(f"{source}: {key!r} is not a key; did you mean root?")
    base = source.parent
    declared = str(raw.get("root") or ".")
    remote = not _on_this_host(str(source))
    if remote and _names_environment(declared):
        raise DeploymentError(
            f"{source}: root {declared!r} names this host's environment; a remote "
            f"deployment's root is taken as written, relative to its folder or a URI"
        )
    if remote and _looks_local(declared):
        raise DeploymentError(
            f"{source}: root {declared!r} is a local path; a remote deployment's "
            f"root is relative to its folder, or a remote URI"
        )
    return Deployment(
        name=name, path=source, root=resolve_location(declared, base, expand=not remote)
    )
