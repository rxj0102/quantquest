"""View-model for the skill-tree page: one plain row per node, no Streamlit."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from core.schema import Question
from core.skill_tree import SkillTree, node_states


@dataclass(frozen=True)
class NodeView:
    """Everything the page shows for one node."""

    id: str
    title: str
    status: str  # "locked" | "unlocked" | "mastered"
    trusted_count: int
    min_trusted: int
    mastery: float
    prerequisites: list[str]
    blocked_by: list[str]
    tracks: dict[str, float]

    @property
    def need_more(self) -> int:
        """Trusted questions still missing before the node can be mastered."""
        return max(0, self.min_trusted - self.trusted_count)

    @property
    def enough_content(self) -> bool:
        """True if the node has the minimum number of trusted questions."""
        return self.need_more == 0


def build_view(tree: SkillTree, playable: Iterable[Question]) -> list[NodeView]:
    """Rows for every node in prerequisite order.

    ``playable`` should come from ``db.list_playable``; anything that is not trusted is ignored
    again by the mastery logic, so a mistake in the caller cannot inflate a count.
    """
    states = node_states(tree, playable)
    rows = []
    for nid in tree.order:
        node, st = tree.nodes[nid], states[nid]
        rows.append(
            NodeView(
                id=nid,
                title=node.title,
                status=st.status,
                trusted_count=st.mastery.trusted_count,
                min_trusted=st.mastery.min_trusted,
                mastery=st.mastery.mastery,
                prerequisites=list(node.prerequisites),
                blocked_by=list(st.blocked_by),
                tracks=dict(node.tracks),
            )
        )
    return rows
