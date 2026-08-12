from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import tomllib

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.git_graph import setup
from src.git_graph.extension import CommitNode, GitGraph, GitRef

SAMPLE_LOG_7_FIELDS = """\
1111111111111111111111111111111111111111|2222222222222222222222222222222222222222 3333333333333333333333333333333333333333|Alice|alice@example.com|1700000000|Merge branch 'feature' into main| (HEAD -> main, tag: v1.0.0)
3333333333333333333333333333333333333333|4444444444444444444444444444444444444444|Bob|bob@example.com|1699990000|Implement feature logic| (feature)
2222222222222222222222222222222222222222|4444444444444444444444444444444444444444|Alice|alice@example.com|1699980000|Docs update on main| (origin/main)
4444444444444444444444444444444444444444||Carol|carol@example.com|1699970000|Initial repository commit|
"""


class TestGitGraphExtension:
    def test_extension_setup_and_registration(self, app_instance) -> None:
        setup(app_instance.api)
        assert app_instance.api is not None

    def test_manifest_schema_and_dependencies(self) -> None:
        toml_path = Path(__file__).parent.parent / "extension.toml"
        assert toml_path.exists(), "extension.toml must exist"
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        assert data["extension"]["id"] == "git_graph"
        assert data["extension"]["version"] == "0.2.0"
        assert data["extension"]["author"] == "tomlin7"
        assert "dependencies" in data
        assert "git" in data["dependencies"]["tools"]
        assert "contributes" in data
        assert len(data["contributes"]["commands"]) >= 3

    def test_legacy_4_field_log_compatibility(self, app_instance) -> None:
        ext = GitGraph(app_instance.api)
        commits = ext.parse_log("abc1234|Bob|2026-10-18|feat: new feature\ndef5678|Carol|2026-10-17|fix: bug")
        assert len(commits) == 2
        assert commits[0]["sha"] == "abc1234"
        assert commits[0]["author"] == "Bob"
        assert commits[0]["date"] == "2026-10-18"
        assert commits[0]["message"] == "feat: new feature"
        assert commits[1]["author"] == "Carol"

    def test_parse_ref_decorations(self, app_instance) -> None:
        ext = GitGraph(app_instance.api)
        refs, head = ext.parse_ref_decorations("(HEAD -> main, origin/main, tag: v1.0.0, feature/login)")
        assert head == "main"
        assert len(refs) == 4

        # Verify HEAD ref
        head_ref = [r for r in refs if r.is_head][0]
        assert head_ref.name == "main"
        assert head_ref.ref_type == "branch"

        # Verify tag
        tag_ref = [r for r in refs if r.ref_type == "tag"][0]
        assert tag_ref.name == "v1.0.0"

        # Verify remote
        remote_ref = [r for r in refs if r.ref_type == "remote"][0]
        assert remote_ref.name == "origin/main"

    def test_parse_7_field_log(self, app_instance) -> None:
        ext = GitGraph(app_instance.api)
        commits = ext.parse_log(SAMPLE_LOG_7_FIELDS)
        assert len(commits) == 4

        # Merge commit
        merge_c = commits[0]
        assert merge_c["sha"] == "1111111111111111111111111111111111111111"
        assert len(merge_c["parents"]) == 2
        assert merge_c["parents"][0] == "2222222222222222222222222222222222222222"
        assert merge_c["parents"][1] == "3333333333333333333333333333333333333333"
        assert merge_c["author"] == "Alice"
        assert "main" in merge_c["refs"]
        assert "v1.0.0" in merge_c["refs"]

        # Root commit
        root_c = commits[3]
        assert root_c["sha"] == "4444444444444444444444444444444444444444"
        assert root_c["parents"] == []

    def test_build_dag_lane_allocation(self, app_instance) -> None:
        ext = GitGraph(app_instance.api)
        raw_commits = ext.parse_log(SAMPLE_LOG_7_FIELDS)
        nodes = ext.build_dag(raw_commits)
        assert len(nodes) == 4

        node_map = {n.sha: n for n in nodes}
        merge_node = node_map["1111111111111111111111111111111111111111"]
        assert merge_node.is_merge is True
        assert merge_node.lane == 0
        assert len(merge_node.routes) >= 2  # Primary lane + merge source

        root_node = node_map["4444444444444444444444444444444444444444"]
        assert root_node.is_root is True

        # Children links verified
        assert "1111111111111111111111111111111111111111" in node_map["2222222222222222222222222222222222222222"].children
        assert "1111111111111111111111111111111111111111" in node_map["3333333333333333333333333333333333333333"].children

    def test_render_ascii_graph(self, app_instance) -> None:
        ext = GitGraph(app_instance.api)
        raw_commits = ext.parse_log(SAMPLE_LOG_7_FIELDS)
        ext.build_dag(raw_commits)
        lines = ext.render_ascii_graph()
        assert len(lines) >= 4
        # Verify rendered text includes commit message and author
        rendered_text = "\n".join(lines)
        assert "Merge branch 'feature' into main" in rendered_text
        assert "Alice" in rendered_text
        assert "Carol" in rendered_text

    def test_filter_by_author_and_branch(self, app_instance) -> None:
        ext = GitGraph(app_instance.api)
        raw_commits = ext.parse_log(SAMPLE_LOG_7_FIELDS)
        ext.build_dag(raw_commits)

        alice_commits = ext.filter_by_author("Alice")
        assert len(alice_commits) == 2

        bob_commits = ext.filter_by_author("Bob")
        assert len(bob_commits) == 1
        assert bob_commits[0].author == "Bob"

        main_branch_commits = ext.filter_by_branch("main")
        assert len(main_branch_commits) == 1

    def test_fetch_git_log_and_refresh(self, app_instance) -> None:
        ext = GitGraph(app_instance.api)
        ext.install()

        with patch.object(ext, "_run_git", return_value=(0, SAMPLE_LOG_7_FIELDS, "")):
            nodes = ext.refresh()
            assert len(nodes) == 4
            assert ext.nodes == nodes

    def test_checkout_command_success_and_failure(self, app_instance) -> None:
        ext = GitGraph(app_instance.api)

        with patch.object(ext, "_run_git", return_value=(0, "Switched to branch 'main'\n", "")):
            success, msg = ext.checkout("main")
            assert success is True
            assert "Switched to branch 'main'" in msg

        with patch.object(ext, "_run_git", return_value=(1, "", "error: pathspec 'bad' did not match\n")):
            success, msg = ext.checkout("bad")
            assert success is False
            assert "error" in msg
