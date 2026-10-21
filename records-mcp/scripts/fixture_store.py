"""Rebuild the fixture store under tests/fixtures/deployments/brindlewick-hives/.

The store is built by latent-records itself, from its own fixture deployment and
doc-intelligence input, with the `hash` test embedder, so no model is downloaded. Run it
inside a latent-records checkout's environment, naming this member's directory:

    uv run --directory <latent-records checkout> python <this file> <records-mcp dir>

Give both paths absolute: `--directory` runs from inside the checkout. It replaces the
fixture's `store/` and its declaration, and prints the latent-records commit the store
was built by (marked dirty when the checkout had changes), for the CHANGELOG.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

NAME = "brindlewick-hives"


def main(member: Path) -> int:
    from latent_records.store import Store

    checkout = Path(sys.modules["latent_records"].__file__ or "").resolve().parents[2]
    sys.path.insert(0, str(checkout / "tests"))
    from conftest import copy_fixture_deployment  # noqa: E402

    target = member / "tests" / "fixtures" / "deployments" / NAME
    with tempfile.TemporaryDirectory() as work:
        deployment = copy_fixture_deployment(Path(work))
        store = Store.from_deployment(deployment)
        store.run(deployment, store.plan(deployment))
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        shutil.copy(deployment.path, target / f"{NAME}.yaml")
        shutil.copytree(deployment.store_dir, target / "store")
    commit = subprocess.run(
        ["git", "-C", str(checkout), "describe", "--always", "--dirty"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    print(f"wrote {target} from latent-records {commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
