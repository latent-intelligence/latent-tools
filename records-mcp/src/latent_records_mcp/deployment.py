"""The deployment folder a server is given, read for serving and for nothing else.

A deployment is a folder holding `<folder name>.yaml`: a local path, or any URI fsspec
opens, such as the copy `deployment sync` writes to a bucket. A server needs two things
from the declaration: `schema_version`, which must be one it knows, and `output:`, where
the products live, resolved against the folder wherever it is; the store is
`<output>/store`, and its own `store.json` names its id. A declaration written before
the store renamed it says `root:`, and is read the same; one naming both is refused. A
`name:` that differs from the file's is refused, as the store does. Everything else in
the declaration (inputs, chunking, spaces, mirror) is the authoring side's and is not
read, so a declaration that grows a key still serves; the one typo it catches is a
case-variant of `output` or `root`, which would otherwise serve the wrong folder in
silence. A name is refused: looking one up is for the store's own authoring commands.
To serve a store with no declaration, `--store` names its folder.

A declaration read from another host is taken as written: an output with `~` or `$VAR`
is refused rather than expanded with this host's environment, since no other host's
values could be meant, and a value expanded here could carry this process's secrets into
a request path. A variable the declaration's own `vars:` sets is part of what is
written, and is read. On this host, a variable's value is the environment's, else the
nearest `.env` beside or above the declaration, else its `vars:` (the store's
`variables.py`).

Adapted from latent-records' `deployment.py` (fe6e28c), plus the constants the copied
`store_json` and `embedders` modules import. It reads fewer keys and so refuses less: it
serves a declaration the store refuses for lacking `store_id` or inputs, for a key the
store does not know, or for saying `root:`. It refuses a name, a folder without a
declaration, a `schema_version` it does not know, a `name:` that differs from the
folder's, a case-variant of `output` or `root`, an output that needs this host's
environment or disk when the declaration is remote, a local output naming an unset
variable, and a URI scheme this install cannot open, some of these in other words than
the store's.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import fsspec  # type: ignore[import-untyped]
import yaml
from upath import UPath

from .errors import DeploymentError
from .locations import location
from .variables import _WORKING, Variable, declared_vars, variables

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
    path: UPath
    root: UPath
    declared_root: str = "."
    """``output:`` as written, before its variables were expanded."""
    variables: tuple[Variable, ...] = ()
    """Each variable ``output:`` uses or ``vars:`` defines, and where it came from."""

    @property
    def store_dir(self) -> UPath:
        return self.root / "store"

    @property
    def is_remote(self) -> bool:
        return "://" in str(self.root)


def _looks_local(value: str) -> bool:
    """Whether a declared location can only mean the writing host's own disk."""
    if "://" in value:
        return _scheme(value) in LOCAL_SCHEMES
    return Path(value).is_absolute() or value.startswith(("/", "\\"))


def _names_environment(value: str) -> bool:
    """Whether a declared location would need this host's environment to resolve."""
    return "://" not in value and (value.startswith("~") or "$" in value)


def _refuse_climb(written: str, value: str, where: str) -> None:
    """Refuse a short location a variable made climb out with `..`: the value is put
    in place first, then read as if written that way, as the store does."""
    short = not (
        value.startswith(("/", "\\", "~", "$"))
        or "://" in value
        or re.match(r"^[A-Za-z]:", value)
    )
    if "$" in written and short and ".." in value.replace("\\", "/").split("/"):
        raise DeploymentError(
            f"{where}: {value!r}, its variable read, climbs out of the deployment's "
            "folder; write the location in full",
            reason="parent-segment",
        )


def resolve_location(
    value: str,
    base: UPath,
    *,
    expand: bool = True,
    values: Mapping[str, str] | None = None,
) -> UPath:
    """A path from the declaration: a URI as given, anything else against the file.
    A variable is read from ``values`` (the deployment's), else the environment."""
    expanded = _expand(value, values) if expand else value
    if expand:
        _refuse_climb(value, expanded, value)
    if expand and "$" in expanded:
        raise DeploymentError(f"{value!r} names an environment variable that is unset")
    if "://" in expanded:
        return _upath(expanded)
    path = Path(expanded)
    return location(path if path.is_absolute() else base / path).resolve()


#: A variable as `os.path.expandvars` reads one: ``$NAME`` or ``${NAME}``.
VARIABLE = re.compile(r"\$(\w+|\{[^}]*\})")


def root_variables(deployment: Deployment) -> list[str]:
    """The variables the declaration's ``output:`` names and takes from the
    environment, once each: what a process started with another environment needs to
    find the same output. One from a ``.env`` or ``vars:`` travels with the YAML."""
    beside = {v.name for v in deployment.variables if v.source != "environment"}
    names = (n.strip("{}") for n in VARIABLE.findall(deployment.declared_root))
    return [n for n in dict.fromkeys(names) if n not in beside]


def _expand(value: str, values: Mapping[str, str] | None = None) -> str:
    """Variables first, from ``values`` then the environment as exported (never an
    empty value, or a name a working-folder ``.env`` set), then ``~``, so a ``~`` a
    variable holds is expanded too. A name set nowhere is left as written."""
    known = {k: v for k, v in os.environ.items() if v and k not in _WORKING} | dict(
        values or {}
    )
    value = VARIABLE.sub(lambda m: known.get(m.group(1).strip("{}"), m.group(0)), value)
    return os.path.expanduser(value)


def _upath(uri: str) -> UPath:
    try:
        return location(uri)
    except ValueError as exc:
        raise DeploymentError(
            f"{uri!r}: {_scheme(uri)}:// is not a scheme fsspec knows"
        ) from exc


def load_deployment_folder(folder: str) -> Deployment:
    """Load the deployment whose folder is `folder`, for serving its ``store_dir``.

    A declaration read from another host may not put ``output:`` on this host's disk:
    no other host could follow it.
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
        directory = location(folder)
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
        if "://" not in folder and directory.is_file():
            raise DeploymentError(
                f"{directory} is a file; give the folder that holds it"
            ) from exc
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


def _parse(text: str, source: UPath) -> Deployment:
    """A declaration's text, read from `source`: its version, name and output."""
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
        lowered = str(key).lower()
        if lowered in ("output", "root") and key != lowered:
            raise DeploymentError(
                f"{source}: {key!r} is not a key; did you mean output?"
            )
    if "output" in raw and "root" in raw:
        raise DeploymentError(
            f"{source}: output and root name one thing; keep output, root's newer name"
        )
    base = source.parent
    declared = str(raw.get("output") or raw.get("root") or ".")
    remote = not _on_this_host(str(source))
    used = {n.strip("{}") for n in VARIABLE.findall(declared)}
    found_vars = variables(
        None if remote else Path(base.path),
        declared_vars(raw.get("vars"), str(source)),
        used,
        {} if remote else os.environ,
    )
    values = {v.name: v.value for v in found_vars}
    if remote:
        # What the declaration's own `vars:` sets is written in it; nothing else is.
        written = declared
        declared = VARIABLE.sub(
            lambda m: values.get(m.group(1).strip("{}"), m.group(0)), declared
        )
        _refuse_climb(written, declared, f"{source}: output")
    if remote and _names_environment(declared):
        raise DeploymentError(
            f"{source}: output {declared!r} names this host's environment; a remote "
            f"deployment's output is taken as written, relative to its folder or a URI"
        )
    if remote and _looks_local(declared):
        raise DeploymentError(
            f"{source}: output {declared!r} is a local path; a remote deployment's "
            f"output is relative to its folder, or a remote URI"
        )
    return Deployment(
        name=name,
        path=source,
        root=resolve_location(declared, base, expand=not remote, values=values),
        declared_root=str(raw.get("output") or raw.get("root") or "."),
        variables=found_vars,
    )
