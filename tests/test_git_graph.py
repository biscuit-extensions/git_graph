from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.git_graph import setup
from src.git_graph.extension import GitGraph


class TestGitGraphExtension:
    def test_extension_setup(self, app_instance) -> None:
        setup(app_instance.api)
        ext = GitGraph(app_instance.api)
        commits = ext.parse_log("abc1234|Bob|2026-10-18|feat: new feature\ndef5678|Carol|2026-10-17|fix: bug")
        assert len(commits) == 2
        assert commits[0]["author"] == "Bob"
