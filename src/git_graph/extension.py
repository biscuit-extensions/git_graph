from __future__ import annotations

import os
import subprocess
import time
import typing
from dataclasses import dataclass, field
from pathlib import Path

from biscuit.extensions import Extension

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


@dataclass
class GitRef:
    """Represents a Git reference (branch, tag, HEAD)."""

    name: str
    ref_type: str  # "head", "branch", "remote", "tag"
    is_head: bool = False


@dataclass
class GraphRoute:
    """Represents a connection line between lanes in the graph DAG."""

    from_lane: int
    to_lane: int
    route_type: str  # "continue", "branch", "merge"


@dataclass
class CommitNode:
    """Represents a node in the Git commit DAG."""

    sha: str
    parents: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    author: str = ""
    author_mail: str = ""
    timestamp: int = 0
    message: str = ""
    refs: list[GitRef] = field(default_factory=list)
    lane: int = 0
    color_index: int = 0
    routes: list[GraphRoute] = field(default_factory=list)
    date_str: str = ""

    @property
    def short_sha(self) -> str:
        return self.sha[:7] if self.sha else ""

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1

    @property
    def is_root(self) -> bool:
        return len(self.parents) == 0


class GitGraph(Extension):
    """Visual commit history graph and interactive branch DAG tree viewer."""

    GRAPH_COLORS = [
        "#007acc",  # blue
        "#28a745",  # green
        "#d73a49",  # red
        "#6f42c1",  # purple
        "#f66a0a",  # orange
        "#005cc5",  # navy
        "#b08800",  # gold
        "#22863a",  # emerald
    ]

    def __init__(self, api: ExtensionsAPI) -> None:
        super().__init__(api)
        self.api = api
        self.nodes: list[CommitNode] = []
        self.node_by_sha: dict[str, CommitNode] = {}
        self.branches: list[str] = []
        self.tags: list[str] = []
        self.current_branch: str | None = None

        if hasattr(self.api, "logger") and self.api.logger:
            self.api.logger.info("Git Graph extension initialised.")

    def _run_git(self, args: list[str], cwd: str | None = None) -> tuple[int, str, str]:
        """Execute a git command safely and return (returncode, stdout, stderr)."""
        try:
            res = subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                cwd=cwd,
                check=False,
            )
            return res.returncode, res.stdout, res.stderr
        except FileNotFoundError:
            return -1, "", "git executable not found on PATH"
        except Exception as exc:
            return -1, "", str(exc)

    def parse_ref_decorations(self, ref_str: str) -> tuple[list[GitRef], str | None]:
        """Parse git %d decoration string (e.g. ' (HEAD -> main, origin/main, tag: v1.0)')."""
        refs: list[GitRef] = []
        current_head: str | None = None
        cleaned = ref_str.strip().strip("()")
        if not cleaned:
            return refs, current_head

        for item in cleaned.split(","):
            part = item.strip()
            if not part:
                continue

            if "->" in part:
                head_part, target = part.split("->", 1)
                target_name = target.strip()
                refs.append(GitRef(name=target_name, ref_type="branch", is_head=True))
                current_head = target_name
            elif part.startswith("tag:"):
                tag_name = part[4:].strip()
                refs.append(GitRef(name=tag_name, ref_type="tag"))
            elif part.startswith("refs/tags/"):
                tag_name = part[10:].strip()
                refs.append(GitRef(name=tag_name, ref_type="tag"))
            elif "/" in part and not part.startswith("refs/heads/"):
                refs.append(GitRef(name=part, ref_type="remote"))
            else:
                branch_name = part.replace("refs/heads/", "")
                refs.append(GitRef(name=branch_name, ref_type="branch"))

        return refs, current_head

    def parse_commit_line(self, line: str) -> dict | None:
        """Parse a pipe-separated commit line.

        Supports both standard 7-field format (sha|parents|author|email|timestamp|message|refs)
        and legacy 4-field format (sha|author|date|message).
        """
        parts = line.strip().split("|")
        if len(parts) >= 7:
            sha = parts[0].strip()
            parents_str = parts[1].strip()
            parents = parents_str.split() if parents_str else []
            author = parts[2].strip()
            author_mail = parts[3].strip()
            ts_str = parts[4].strip()
            timestamp = int(ts_str) if ts_str.isdigit() else 0
            message = parts[5].strip()
            ref_decorations = parts[6].strip()
            refs, head_branch = self.parse_ref_decorations(ref_decorations)
            date_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp)) if timestamp else ""

            return {
                "sha": sha,
                "parents": parents,
                "author": author,
                "author_mail": author_mail,
                "date": date_str,
                "timestamp": timestamp,
                "message": message,
                "refs": [r.name for r in refs],
                "ref_objects": refs,
            }

        if len(parts) >= 4:
            return {
                "sha": parts[0].strip(),
                "author": parts[1].strip(),
                "date": parts[2].strip(),
                "message": parts[3].strip(),
                "parents": [],
                "refs": [],
                "ref_objects": [],
            }
        return None

    def parse_log(self, raw_log: str) -> list[dict]:
        """Parse raw git log output into commit dictionaries."""
        commits = []
        for line in raw_log.splitlines():
            c = self.parse_commit_line(line)
            if c:
                commits.append(c)
        return commits

    def build_dag(self, raw_commits: list[dict]) -> list[CommitNode]:
        """Construct the commit DAG and assign topological display lanes."""
        nodes: list[CommitNode] = []
        node_by_sha: dict[str, CommitNode] = {}

        # 1. Instantiate all commit nodes
        for item in raw_commits:
            sha = item.get("sha", "")
            parents = item.get("parents", [])
            node = CommitNode(
                sha=sha,
                parents=parents,
                author=item.get("author", ""),
                author_mail=item.get("author_mail", ""),
                timestamp=item.get("timestamp", 0),
                message=item.get("message", ""),
                refs=item.get("ref_objects", []),
                date_str=item.get("date", ""),
            )
            nodes.append(node)
            node_by_sha[sha] = node

        # 2. Wire child links
        for node in nodes:
            for p_sha in node.parents:
                parent_node = node_by_sha.get(p_sha)
                if parent_node and node.sha not in parent_node.children:
                    parent_node.children.append(node.sha)

        # 3. Topological Lane Allocation Algorithm
        active_lanes: list[str | None] = []
        for node in nodes:
            routes: list[GraphRoute] = []

            # Determine lane for this commit
            if node.sha in active_lanes:
                node_lane = active_lanes.index(node.sha)
            else:
                # Find first None slot or append
                try:
                    node_lane = active_lanes.index(None)
                    active_lanes[node_lane] = node.sha
                except ValueError:
                    node_lane = len(active_lanes)
                    active_lanes.append(node.sha)

            node.lane = node_lane
            node.color_index = node_lane % len(self.GRAPH_COLORS)

            # Update lanes for parents
            if not node.parents:
                # Root commit: lane terminates
                active_lanes[node_lane] = None
            else:
                # Primary parent occupies the same lane
                p0 = node.parents[0]
                routes.append(GraphRoute(from_lane=node_lane, to_lane=node_lane, route_type="continue"))
                active_lanes[node_lane] = p0

                # Additional parents (merge sources)
                for p_extra in node.parents[1:]:
                    if p_extra in active_lanes:
                        target_lane = active_lanes.index(p_extra)
                        routes.append(
                            GraphRoute(from_lane=node_lane, to_lane=target_lane, route_type="merge")
                        )
                    else:
                        try:
                            new_lane = active_lanes.index(None)
                            active_lanes[new_lane] = p_extra
                        except ValueError:
                            new_lane = len(active_lanes)
                            active_lanes.append(p_extra)
                        routes.append(
                            GraphRoute(from_lane=node_lane, to_lane=new_lane, route_type="branch")
                        )

            node.routes = routes

            # Prune trailing None lanes
            while active_lanes and active_lanes[-1] is None:
                active_lanes.pop()

        self.nodes = nodes
        self.node_by_sha = node_by_sha
        return nodes

    def render_ascii_graph(self, max_commits: int = 50) -> list[str]:
        """Render a visual ASCII graph representation of the commit DAG."""
        lines = []
        commits_to_render = self.nodes[:max_commits]
        max_lane = max((n.lane for n in commits_to_render), default=0)

        for node in commits_to_render:
            symbols = []
            for lane_idx in range(max_lane + 1):
                if lane_idx == node.lane:
                    symbols.append("●" if not node.is_merge else "⬡")
                else:
                    symbols.append("│")

            graph_col = " ".join(symbols)
            ref_badge = f" [{' | '.join(r.name for r in node.refs)}]" if node.refs else ""
            line = f"{graph_col}  {node.short_sha}{ref_badge} {node.message} ({node.author}, {node.date_str or 'recent'})"
            lines.append(line)

            # Render route connectors if node is a merge or has branches
            merge_routes = [r for r in node.routes if r.route_type in ("merge", "branch")]
            if merge_routes:
                connector = [" "] * (max_lane * 2 + 1)
                for r in merge_routes:
                    idx = min(r.from_lane, r.to_lane) * 2 + 1
                    if idx < len(connector):
                        connector[idx] = "╲" if r.from_lane < r.to_lane else "╱"
                lines.append("".join(connector))

        return lines

    def fetch_git_log(self, max_commits: int = 100, cwd: str | None = None) -> list[CommitNode]:
        """Query git log --graph DAG from repository and construct nodes."""
        fmt = "%H|%P|%an|%ae|%at|%s|%d"
        args = ["log", "--all", "--topo-order", f"--format={fmt}", f"-n{max_commits}"]
        code, stdout, stderr = self._run_git(args, cwd=cwd)
        if code != 0:
            if hasattr(self.api, "logger") and self.api.logger:
                self.api.logger.warning(f"Git Graph fetch failed: {stderr.strip()}")
            return []

        raw_commits = self.parse_log(stdout)
        return self.build_dag(raw_commits)

    def checkout(self, ref_or_sha: str, cwd: str | None = None) -> tuple[bool, str]:
        """Checkout a branch, tag, or commit."""
        code, stdout, stderr = self._run_git(["checkout", ref_or_sha], cwd=cwd)
        success = code == 0
        msg = stdout if success else stderr
        if hasattr(self.api, "notifications") and self.api.notifications:
            if success:
                self.api.notifications.info(f"Checked out {ref_or_sha}")
            else:
                self.api.notifications.error(f"Checkout failed: {stderr.strip()}")
        return success, msg

    def filter_by_author(self, author_query: str) -> list[CommitNode]:
        """Filter loaded commit nodes by author name substring."""
        q = author_query.lower()
        return [n for n in self.nodes if q in n.author.lower()]

    def filter_by_branch(self, branch_name: str) -> list[CommitNode]:
        """Filter loaded commit nodes by branch name reference."""
        return [n for n in self.nodes if any(r.name == branch_name for r in n.refs)]

    def refresh(self, cwd: str | None = None) -> list[CommitNode]:
        """Refresh commit log and rebuild DAG."""
        nodes = self.fetch_git_log(cwd=cwd)
        if hasattr(self.api, "notifications") and self.api.notifications:
            self.api.notifications.info(f"Git Graph refreshed: {len(nodes)} commits loaded.")
        return nodes

    def install(self) -> None:
        """Register Git Graph commands and views."""
        if hasattr(self.api, "commands") and hasattr(self.api.commands, "register_command"):
            self.api.commands.register_command("git_graph.view", self.refresh)
            self.api.commands.register_command("git_graph.refresh", self.refresh)
            self.api.commands.register_command("git_graph.checkout_branch", self.checkout)

        if hasattr(self.api, "logger") and self.api.logger:
            self.api.logger.info("Git Graph extension installed.")
