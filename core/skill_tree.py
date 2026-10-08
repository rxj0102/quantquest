"""Skill tree: nodes, prerequisites, mastery and unlock logic.

The tree lives in ``data/skill_tree.yaml``; extending it means adding a node block there.
It is validated on load: unique ids, known prerequisites, no cycles, complete track weights.
``validate_questions`` additionally checks that every question's ``node_id`` is a real node.

Mastery of a node (computed from ``solve_stats``, trusted questions only)
------------------------------------------------------------------------
* Only questions with ``status == trusted`` count. Fresh, flagged and retired questions never
  contribute, however well they were answered.
* ``coverage``  = trusted questions with at least one correct answer / trusted questions
* ``accuracy``  = total correct / total attempts over the node's trusted questions
* ``mastery``   = coverage x accuracy
* A node needs at least ``min_trusted`` trusted questions (default 3) before it can be mastered,
  so a node cannot be mastered on one or two lucky answers or because content is missing.
* mastered = enough content and ``mastery >= mastery_threshold`` (default 0.6)

Status: a node is ``locked`` while any prerequisite is not (unlocked and) mastered, ``mastered``
once its own mastery qualifies, and ``unlocked`` otherwise (roots are unlocked from the start).
Locking is transitive: if a node regresses, everything below it locks again.

Caveat: ``solve_stats`` is a per-question total, not per user. That matches the single local
user of today; ``node_states`` takes the questions as an argument so per-user stats can be
supplied later without changing the tree logic.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from core.schema import Question, Status

TRACKS = ("behavioral_quant", "quant_research", "risk_quant")
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "skill_tree.yaml"


class TreeError(ValueError):
    """The skill tree file is invalid, or questions do not match it."""


class SkillNode(BaseModel):
    """One topic in the tree."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    title: str = Field(min_length=1)
    tracks: dict[str, float]
    prerequisites: list[str] = Field(default_factory=list)
    mastery_threshold: float | None = Field(default=None, gt=0, le=1)
    min_trusted: int | None = Field(default=None, ge=1)

    @field_validator("tracks")
    @classmethod
    def _tracks(cls, value: dict[str, float]) -> dict[str, float]:
        if set(value) != set(TRACKS):
            raise ValueError(f"tracks must be exactly {list(TRACKS)}, got {sorted(value)}")
        for name, weight in value.items():
            if not 0 <= weight <= 1:
                raise ValueError(f"track weight for {name!r} must be between 0 and 1, got {weight}")
        return value


class SkillTree(BaseModel):
    """A validated tree. ``order`` lists node ids with prerequisites first."""

    model_config = ConfigDict(extra="forbid")

    nodes: dict[str, SkillNode]
    order: list[str]
    min_trusted_default: int = Field(default=3, ge=1)
    mastery_threshold_default: float = Field(default=0.6, gt=0, le=1)

    def threshold(self, node_id: str) -> float:
        """Mastery threshold for a node."""
        t = self.nodes[node_id].mastery_threshold
        return self.mastery_threshold_default if t is None else t

    def min_trusted(self, node_id: str) -> int:
        """Trusted questions a node needs before it can be mastered."""
        m = self.nodes[node_id].min_trusted
        return self.min_trusted_default if m is None else m

    def validate_questions(self, questions: Iterable[Question]) -> None:
        """Raise ``TreeError`` if any question points at a node that does not exist."""
        bad = sorted({(q.node_id, q.id) for q in questions if q.node_id not in self.nodes})
        if bad:
            listing = ", ".join(f"{node!r} (question {qid})" for node, qid in bad)
            raise TreeError(f"questions reference unknown skill-tree nodes: {listing}")


def _find_cycle(nodes: dict[str, SkillNode]) -> list[str] | None:
    state: dict[str, int] = {}  # 1 = on the current path, 2 = done
    path: list[str] = []

    def visit(nid: str) -> list[str] | None:
        state[nid] = 1
        path.append(nid)
        for pre in nodes[nid].prerequisites:
            if state.get(pre) == 1:
                return [*path[path.index(pre) :], pre]
            if pre not in state and (found := visit(pre)):
                return found
        path.pop()
        state[nid] = 2
        return None

    for nid in nodes:
        if nid not in state and (found := visit(nid)):
            return found
    return None


def parse_tree(raw: Any) -> SkillTree:
    """Validate a parsed YAML document and build the tree. Raises ``TreeError``."""
    if not isinstance(raw, dict):
        raise TreeError("skill tree must be a mapping with a 'nodes' list")
    unknown = set(raw) - {"nodes", "min_trusted_default", "mastery_threshold_default"}
    if unknown:
        raise TreeError(f"unknown top-level keys: {sorted(unknown)}")
    raw_nodes = raw.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise TreeError("skill tree needs at least one node")
    nodes: dict[str, SkillNode] = {}
    for item in raw_nodes:
        try:
            node = SkillNode.model_validate(item)
        except ValidationError as exc:
            label = item.get("id", "?") if isinstance(item, dict) else "?"
            raise TreeError(f"node {label}: {exc}") from exc
        if node.id in nodes:
            raise TreeError(f"duplicate node id {node.id}")
        nodes[node.id] = node
    for node in nodes.values():
        if len(set(node.prerequisites)) != len(node.prerequisites):
            raise TreeError(f"node {node.id}: duplicate prerequisite")
        for pre in node.prerequisites:
            if pre not in nodes:
                raise TreeError(f"node {node.id}: unknown prerequisite {pre!r}")
    if cycle := _find_cycle(nodes):
        raise TreeError("prerequisite cycle: " + " -> ".join(cycle))
    order: list[str] = []
    while len(order) < len(nodes):  # stable topological order: file order among ready nodes
        ready = next(
            n for n in nodes if n not in order and all(p in order for p in nodes[n].prerequisites)
        )
        order.append(ready)
    defaults = {k: raw[k] for k in ("min_trusted_default", "mastery_threshold_default") if k in raw}
    try:
        return SkillTree(nodes=nodes, order=order, **defaults)
    except ValidationError as exc:
        raise TreeError(str(exc)) from exc


def load_tree(path: Path = DEFAULT_PATH) -> SkillTree:
    """Load and validate the skill tree YAML file."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        raise TreeError(f"{path}: cannot read skill tree: {exc}") from exc
    try:
        return parse_tree(raw)
    except TreeError as exc:
        raise TreeError(f"{path}: {exc}") from exc


# -- mastery and unlocking ---------------------------------------------------------------------


class NodeMastery(BaseModel):
    """How well one node is learned, from trusted questions only."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    trusted_count: int
    min_trusted: int
    covered: int
    attempts: int
    correct: int
    coverage: float
    accuracy: float
    mastery: float
    threshold: float

    @property
    def enough_content(self) -> bool:
        """True if the node has the minimum number of trusted questions."""
        return self.trusted_count >= self.min_trusted

    @property
    def mastered(self) -> bool:
        """Enough trusted content and mastery at or above the threshold."""
        return self.enough_content and self.mastery + 1e-12 >= self.threshold


class NodeState(BaseModel):
    """A node's mastery and whether the user can work on it."""

    model_config = ConfigDict(frozen=True)

    status: str  # "locked" | "unlocked" | "mastered"
    mastery: NodeMastery
    blocked_by: list[str]


def _mastery(tree: SkillTree, node_id: str, trusted: list[Question]) -> NodeMastery:
    n = len(trusted)
    attempts = sum(q.solve_stats.attempts for q in trusted)
    correct = sum(q.solve_stats.correct for q in trusted)
    covered = sum(1 for q in trusted if q.solve_stats.correct >= 1)
    coverage = covered / n if n else 0.0
    accuracy = correct / attempts if attempts else 0.0
    return NodeMastery(
        node_id=node_id,
        trusted_count=n,
        min_trusted=tree.min_trusted(node_id),
        covered=covered,
        attempts=attempts,
        correct=correct,
        coverage=coverage,
        accuracy=accuracy,
        mastery=coverage * accuracy,
        threshold=tree.threshold(node_id),
    )


def _trusted_by_node(questions: Iterable[Question]) -> dict[str, list[Question]]:
    grouped: dict[str, list[Question]] = {}
    for q in questions:
        if q.status is Status.TRUSTED:  # fresh, flagged and retired never count
            grouped.setdefault(q.node_id, []).append(q)
    return grouped


def node_mastery(tree: SkillTree, node_id: str, questions: Iterable[Question]) -> NodeMastery:
    """Mastery of one node. ``questions`` may contain any statuses; only trusted ones count."""
    if node_id not in tree.nodes:
        raise TreeError(f"unknown node {node_id!r}")
    return _mastery(tree, node_id, _trusted_by_node(questions).get(node_id, []))


def node_states(tree: SkillTree, questions: Iterable[Question]) -> dict[str, NodeState]:
    """Status of every node (in prerequisite order)."""
    grouped = _trusted_by_node(questions)
    mastery = {nid: _mastery(tree, nid, grouped.get(nid, [])) for nid in tree.order}
    states: dict[str, NodeState] = {}
    for nid in tree.order:
        # A prerequisite counts only if it is itself unlocked and mastered (its status), so a
        # regression in a grandparent re-locks every descendant instead of leaving gaps.
        blocked = [p for p in tree.nodes[nid].prerequisites if states[p].status != "mastered"]
        status = "locked" if blocked else ("mastered" if mastery[nid].mastered else "unlocked")
        states[nid] = NodeState(status=status, mastery=mastery[nid], blocked_by=blocked)
    return states


# -- content report ----------------------------------------------------------------------------


@dataclass
class ReportRow:
    """One line of the content report."""

    node_id: str
    trusted_count: int
    min_trusted: int
    mastery: float
    status: str


@dataclass
class ContentReport:
    """Trusted-question counts per node and the nodes that content gaps hold back."""

    rows: list[ReportRow] = field(default_factory=list)
    below_minimum: list[str] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)
    blockers: dict[str, list[str]] = field(default_factory=dict)

    def format(self) -> str:
        """Table of trusted counts per node plus a list of content gaps."""
        width = max((len(r.node_id) for r in self.rows), default=10)
        lines = [f"{'node'.ljust(width)}  trusted/min  mastery  status"]
        for r in self.rows:
            short = r.min_trusted - r.trusted_count
            note = f"  need {short} more" if short > 0 else ""
            lines.append(
                f"{r.node_id.ljust(width)}  {f'{r.trusted_count}/{r.min_trusted}'.ljust(11)}  "
                f"{r.mastery:7.2f}  {r.status}{note}"
            )
        if not self.below_minimum:
            lines.append("All nodes have the minimum number of trusted questions.")
        else:
            lines.append(
                f"Below the minimum (cannot be mastered yet): {', '.join(self.below_minimum)}"
            )
        for nid in self.unreachable:
            lines.append(
                f"Unreachable until content is added: {nid} (needs {', '.join(self.blockers[nid])})"
            )
        return "\n".join(lines)


def content_report(tree: SkillTree, questions: Iterable[Question]) -> ContentReport:
    """Trusted question counts per node, the nodes under the minimum, and what they block."""
    questions = list(questions)
    states = node_states(tree, questions)
    report = ContentReport()
    for nid in tree.order:
        m = states[nid].mastery
        report.rows.append(
            ReportRow(nid, m.trusted_count, m.min_trusted, m.mastery, states[nid].status)
        )
        if not m.enough_content:
            report.below_minimum.append(nid)
    short = set(report.below_minimum)

    def short_ancestors(nid: str) -> list[str]:
        found: list[str] = []
        for pre in tree.nodes[nid].prerequisites:
            if pre in short:
                found.append(pre)
            found += [a for a in short_ancestors(pre) if a not in found]
        return found

    for nid in tree.order:
        blockers = short_ancestors(nid)
        if blockers:
            report.unreachable.append(nid)
            report.blockers[nid] = blockers
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI: print trusted-question counts per node. ``python -m core.skill_tree --db PATH``."""
    from core import db

    parser = argparse.ArgumentParser(description="Skill tree content report")
    parser.add_argument("--db", default="quantquest.db", help="SQLite database path")
    parser.add_argument("--tree", default=str(DEFAULT_PATH), help="skill tree YAML file")
    args = parser.parse_args(argv)
    tree = load_tree(Path(args.tree))
    conn = db.connect(args.db)
    try:
        questions = db.list_questions(conn)
    finally:
        conn.close()
    try:
        tree.validate_questions(questions)
    except TreeError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(content_report(tree, questions).format())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
