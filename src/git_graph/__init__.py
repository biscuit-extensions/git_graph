from __future__ import annotations

import typing

from .extension import GitGraph

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


def setup(api: ExtensionsAPI) -> None:
    """Entrypoint for Biscuit to load the extension."""
    api.register("git_graph", GitGraph(api))
