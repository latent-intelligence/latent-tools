# Verbatim copy of kb-wiki at 186a4af, below this header:
#   src/kb_wiki/variables.py
# Do not edit; sync from the store. tests/test_verbatim.py compares it.

"""Where the variables in a deployment's locations come from.

A location may start with ``${NAME}``. Its value is the first of: the process
environment, as exported; the nearest ``.env``, beside the declaration or in the
nearest folder above it that has one (only that file, read without changing the
environment or expanding a variable in it); the declaration's ``vars:``. An empty
value is unset.
The same rule in every package: the shared storage cases. A ``.env`` the command
loads from its working folder, for its own settings, never counts as the environment
for a location: run from one pipeline's folder, a command must not read another's data.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, find_dotenv, load_dotenv

from .errors import DeploymentError

NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")

_WORKING: set[str] = set()
"""The names :func:`load_working_dotenv` set: never the environment for a location."""


def load_working_dotenv() -> None:
    """Load the ``.env`` in the working folder or a folder above into the
    environment, for the command's own settings; a variable already set wins. The
    names it sets are not the environment for a location."""
    found = find_dotenv(usecwd=True)
    if not found:
        return
    before = set(os.environ)
    load_dotenv(found)
    _WORKING.update(set(os.environ) - before)


@dataclass(frozen=True)
class Variable:
    name: str
    value: str
    """As its source gives it, before it is read as a location."""
    source: str
    """``environment``, ``vars``, or the path of the ``.env`` it came from."""


def declared_vars(raw: Any, where: str) -> dict[str, str]:
    """The declaration's ``vars:``, checked.

    Raises:
        DeploymentError: ``vars-invalid``: not a mapping, a name not upper case,
            digits and ``_``, or a value that is not a location or uses a variable.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise DeploymentError(
            f"{where}: vars must map names to locations", reason="vars-invalid"
        )
    for name, value in raw.items():
        if not isinstance(name, str) or not NAME.match(name):
            raise DeploymentError(
                f"{where}: vars: {name!r} is not a name; use upper case, digits and _",
                reason="vars-invalid",
            )
        if not isinstance(value, str) or not value:
            raise DeploymentError(
                f"{where}: vars: {name} must be a path or a URI", reason="vars-invalid"
            )
        if "$" in value:
            raise DeploymentError(
                f"{where}: vars: {name} may not use a variable; write it in full",
                reason="vars-invalid",
            )
    return dict(raw)


def nearest_dotenv(folder: Path) -> Path | None:
    """The ``.env`` in ``folder``, else in the nearest folder above that has one."""
    for parent in (folder, *folder.parents):
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
    return None


def variables(
    folder: Path | None,
    declared: Mapping[str, str],
    used: Iterable[str],
    environ: Mapping[str, str],
) -> tuple[Variable, ...]:
    """Each name ``used`` by a location or ``declared`` under ``vars:``, with its
    value and where it came from. A name set nowhere is left out, for the location
    that uses it to refuse."""
    names = sorted({*used, *declared})
    if not names:
        return ()
    dotenv = nearest_dotenv(folder) if folder is not None else None
    try:
        from_file = (
            {k: v for k, v in dotenv_values(dotenv, interpolate=False).items() if v}
            if dotenv
            else {}
        )
    except (OSError, UnicodeDecodeError) as exc:
        raise DeploymentError(f"{dotenv}: cannot be read: {exc}") from exc
    found = []
    for name in names:
        if environ.get(name) and name not in _WORKING:
            found.append(Variable(name, environ[name], "environment"))
        elif from_file.get(name):
            if "$" in str(from_file[name]):
                raise DeploymentError(
                    f"{dotenv}: {name} may not use a variable; write it in full",
                    reason="vars-invalid",
                )
            found.append(Variable(name, str(from_file[name]), str(dotenv)))
        elif declared.get(name):
            found.append(Variable(name, declared[name], "vars"))
    return tuple(found)
