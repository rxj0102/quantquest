"""Skill tree page: each node as locked, unlocked or mastered, with its trusted-question count."""

from __future__ import annotations

import streamlit as st

from app import runtime
from core import db
from core.skill_tree import load_tree
from core.skill_view import NodeView, build_view

LABEL = {"locked": "🔒 Locked", "unlocked": "🔓 Unlocked", "mastered": "✅ Mastered"}


def render_node(node: NodeView, titles: dict[str, str]) -> None:
    """One bordered card for one node."""
    with st.container(border=True):
        left, right = st.columns([3, 1])
        left.subheader(node.title)
        right.markdown(f"**{LABEL[node.status]}**")
        st.progress(min(1.0, node.mastery), text=f"Mastery {node.mastery:.0%}")
        st.caption(f"{node.trusted_count}/{node.min_trusted} trusted questions")
        if node.need_more:
            st.caption(
                f"Needs {node.need_more} more trusted question(s) before it can be mastered."
            )
        if node.blocked_by:
            st.caption("Locked until mastered: " + ", ".join(titles[p] for p in node.blocked_by))
        elif node.prerequisites:
            st.caption("Prerequisites: " + ", ".join(titles[p] for p in node.prerequisites))
        tracks = ", ".join(f"{k.replace('_', ' ')} {v:.0%}" for k, v in node.tracks.items())
        st.caption(f"Relevance: {tracks}")


def page() -> None:
    """Render the skill tree."""
    settings = runtime.get_settings()
    tree = load_tree()
    with runtime.open_db(settings) as conn:
        nodes = build_view(tree, db.list_playable(conn))
    st.title("Skill tree")
    counts = {s: sum(n.status == s for n in nodes) for s in LABEL}
    a, b, c = st.columns(3)
    a.metric("Mastered", counts["mastered"])
    b.metric("Unlocked", counts["unlocked"])
    c.metric("Locked", counts["locked"])
    titles = {n.id: n.title for n in nodes}
    for node in nodes:
        render_node(node, titles)
