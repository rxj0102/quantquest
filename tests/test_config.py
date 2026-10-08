import pytest

from core.config import ConfigError, Settings, load_settings


def test_defaults() -> None:
    s = load_settings({})
    assert s.timezone == "America/New_York"
    assert s.ui_numeric_rel_tol == 1e-3
    assert s.user_id == "local" and s.db_path == "quantquest.db"
    assert (s.session_max_cards, s.session_max_new) == (20, 10)
    assert (s.interview_questions, s.interview_minutes) == (5, 20)
    assert s.tz.key == "America/New_York"


def test_environment_overrides() -> None:
    s = load_settings(
        {
            "QQ_TIMEZONE": "Europe/London",
            "QQ_DB_PATH": "/tmp/x.db",
            "QQ_USER_ID": "raj",
            "QQ_UI_REL_TOL": "0.01",
            "QQ_SESSION_MAX_CARDS": "7",
            "QQ_SESSION_MAX_NEW": "3",
            "QQ_INTERVIEW_QUESTIONS": "4",
            "QQ_INTERVIEW_MINUTES": "12",
        }
    )
    assert s.tz.key == "Europe/London" and s.db_path == "/tmp/x.db" and s.user_id == "raj"
    assert s.ui_numeric_rel_tol == 0.01 and (s.session_max_cards, s.session_max_new) == (7, 3)
    assert (s.interview_questions, s.interview_minutes) == (4, 12)


@pytest.mark.parametrize(
    "env",
    [
        {"QQ_TIMEZONE": "Mars/Olympus"},
        {"QQ_TIMEZONE": ""},
        {"QQ_UI_REL_TOL": "-0.1"},
        {"QQ_UI_REL_TOL": "1"},
        {"QQ_UI_REL_TOL": "abc"},
        {"QQ_SESSION_MAX_CARDS": "0"},
        {"QQ_SESSION_MAX_NEW": "-1"},
        {"QQ_INTERVIEW_QUESTIONS": "0"},
        {"QQ_INTERVIEW_MINUTES": "0"},
        {"QQ_INTERVIEW_MINUTES": "x"},
        {"QQ_USER_ID": ""},
    ],
)
def test_invalid_values_are_rejected(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        load_settings(env)


def test_settings_validate_when_built_directly() -> None:
    with pytest.raises(ConfigError):
        Settings(timezone="Nope/Nope")
    assert Settings(ui_numeric_rel_tol=0.0).ui_numeric_rel_tol == 0.0  # strict is allowed
