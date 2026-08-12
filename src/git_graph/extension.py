from __future__ import annotations

import typing

from biscuit.extensions import Extension

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


class GitGraph(Extension):
    """Visual commit history graph and interactive branch DAG tree viewer."""

    def __init__(self, api: ExtensionsAPI) -> None:
        super().__init__(api)
        self.api = api
        self.api.logger.info("Git Graph extension initialised.")

    def parse_commit_line(self, line: str) -> dict | None:
        parts = line.strip().split("|")
        if len(parts) >= 4:
            return {
                "sha": parts[0].strip(),
                "author": parts[1].strip(),
                "date": parts[2].strip(),
                "message": parts[3].strip(),
            }
        return None

    def parse_log(self, raw_log: str) -> list[dict]:
        commits = []
        for line in raw_log.splitlines():
            c = self.parse_commit_line(line)
            if c:
                commits.append(c)
        return commits

    def install(self) -> None:
        self.api.logger.info("Git Graph extension installed.")
