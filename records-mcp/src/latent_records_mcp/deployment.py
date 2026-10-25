"""The deployment folder a server is given, read for serving and for nothing else.

A deployment is a folder holding `<folder name>.yaml`: a local path, or any URI fsspec
opens, such as the copy a store's `just push` writes to a bucket. A server needs two
things from the declaration: `schema_version`, which must be one it knows, and `root:`,
where the products live, resolved against the folder wherever it is; the store is
`<root>/store`, and its own `store.json` names its id. A `name:` that differs from the
file's is refused, as the store does. Everything else in the declaration (inputs,
chunking, spaces) is the authoring side's and is not read, so a declaration that grows a
key still serves; the one typo it catches is a case-variant of `root`, which would
otherwise serve the wrong folder in silence. A name is refused: looking one up is for
the store's own authoring commands.

A declaration read from another host is taken as written: a root with `~` or `$VAR` is
refused rather than expanded with this host's environment, since no other host's values
could be meant, and a value expanded here could carry this process's secrets into a
request path.

Adapted from latent-records' `deployment.py` (e9eb62a), plus the constants the copied
`search`, `store_json` and `embedders` modules import. It reads fewer keys and so
refuses less: it serves a declaration the store refuses for lacking `store_id` or
inputs, or for a key the store does not know. It refuses a name, a folder without a
declaration, a `schema_version` it does not know, a `name:` that differs from the
folder's, a case-variant of `root`, a remote root that needs this host's environment or
disk, a local root naming an unset variable, and a URI scheme this install cannot open,
some of these in other words than the store's.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import fsspec  # type: ignore[import-untyped]
import yaml
from upath import UPath

from .errors import DeploymentError

#: The extra that installs a scheme's filesystem, for the schemes this package is
#: checked against. Any other scheme fsspec knows works once its filesystem is in.
EXTRAS = {
    "s3": "s3",
    "s3a": "s3",
    "gs": "gcs",
    "gcs": "gcs",
    "az": "az",
    "abfs": "az",
    "abfss": "az",
}

#: Schemes that name the reading host's own disk: a local path spelled as a URI.
LOCAL_SCHEMES = {"file", "local"}

SCHEMA_VERSION = 1

TARGETS: dict[str, tuple[str, str]] = {
    "chunks": ("text", "text"),
    "tables": ("text", "tables"),
    "figure_captions": ("text", "figures"),
    "figure_images": ("image", "figures"),
}


@dataclass(frozen=True)
class EmbedderKind:
    modalities: frozenset[str]
    required: frozenset[str]
    optional: frozenset[str] = frozenset()
    extra: str | None = None
    module: str | None = None


EMBEDDERS: dict[str, EmbedderKind] = {
    "hash": EmbedderKind(frozenset({"text", "image"}), frozenset({"dims"})),
    "sentence-transformers": EmbedderKind(
        frozenset({"text"}),
        frozenset({"model"}),
        frozenset({"revision", "query_prefix", "document_prefix"}),
        extra="text",
        module="sentence_transformers",
    ),
    "open-clip": EmbedderKind(
        frozenset({"text", "image"}),
        frozenset({"model", "pretrained"}),
        extra="clip",
        module="open_clip",
    ),
}

NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class Deployment:
    name: str
    path: Path
    root: Path
    declared_root: str = "."
    """``root:`` as written, before its variables were expanded."""

    @property
    def store_dir(self) -> Path:
        return self.root / "store"

    @property
    def is_remote(self) -> bool:
        return "://" in str(self.root)


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
    expanded = _expand(value) if expand else value
    if expand and "$" in expanded:
        raise DeploymentError(f"{value!r} names an environment variable that is unset")
    if "://" in expanded:
        return _upath(expanded)
    path = Path(expanded)
    return (path if path.is_absolute() else base / path).resolve()


#: A variable as `os.path.expandvars` reads one: ``$NAME`` or ``${NAME}``.
VARIABLE = re.compile(r"\$(\w+|\{[^}]*\})")


def root_variables(deployment: Deployment) -> list[str]:
    """The variables the declaration's ``root:`` names, once each: what a process
    started with another environment needs to find the same root."""
    names = (n.strip("{}") for n in VARIABLE.findall(deployment.declared_root))
    return list(dict.fromkeys(names))


def _expand(value: str) -> str:
    """Variables first, then ``~``, so a ``~`` a variable holds is expanded too."""
    return os.path.expanduser(os.path.expandvars(value))


def _upath(uri: str) -> Path:
    try:
        return cast(Path, UPath(uri))
    except ValueError as exc:
        raise DeploymentError(
            f"{uri!r}: {_scheme(uri)}:// is not a scheme fsspec knows"
        ) from exc


def load_deployment_folder(folder: str) -> Deployment:
    """Load the deployment whose folder is `folder`, for serving its ``store_dir``.

    A declaration read from another host may not put ``root:`` on this host's disk: no
    other host could follow it.
    """
    if NAME.match(folder):
        raise DeploymentError(
            f"{folder!r} is a name; give the deployment's folder, a path or a URI "
            f"(./{folder} if it is here)"
        )
    if "://" in folder:
        require_filesystem(folder)
        directory = _upath(folder)
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
                f"install latent-records-mcp[{extra}]"
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
    name = source.stem
    if raw.get("name") not in (None, name):
        raise DeploymentError(
            f"{source}: name {raw['name']!r} differs from the file's; "
            f"a deployment is named by its file"
        )
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
        name=name,
        path=source,
        root=resolve_location(declared, base, expand=not remote),
        declared_root=declared,
    )
