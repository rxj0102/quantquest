import pytest
import streamlit as st

from core import db
from core.schema import AnswerType, Status, Verification
from tests.conftest import add_question


@pytest.fixture(autouse=True)
def _fresh_streamlit_caches():
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


@pytest.fixture
def ui_db(tmp_path, curated):
    """A file database where all 19 verifiable curated questions are trusted."""
    path = tmp_path / "qq.db"
    conn = db.connect(path)
    db.upsert_questions(conn, curated)
    ok = Verification(method="test_fixture", result="pass")
    for q in curated:
        if q.answer_type is not AnswerType.TEXT:
            db.set_verification(conn, q.id, ok, Status.TRUSTED)
    conn.close()
    return path


@pytest.fixture
def db_conn(ui_db):
    conn = db.connect(ui_db)
    yield conn
    conn.close()


@pytest.fixture
def hidden_db(ui_db):
    """The trusted pool plus fresh, flagged and retired questions carrying unique markers."""
    conn = db.connect(ui_db)
    for status, marker in (
        ("fresh", "MARK-FRESH"),
        ("flagged", "MARK-FLAGGED"),
        ("retired", "MARK-RETIRED"),
    ):
        for node in ("prob.counting", "la.determinants", "prob.conditional"):
            add_question(
                conn,
                f"h{status[0]}{node[0]}x-001",
                status=status,
                node=node,
                prompt=f"{marker} prompt text",
                solution=f"{marker} solution text",
                answer="7",
                difficulty=1,
            )
    conn.close()
    return ui_db
