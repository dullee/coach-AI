from __future__ import annotations

from backend.config import load_dotenv


def test_load_dotenv_reads_keys_and_keeps_shell_values(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n\n"
        "COACH_TEST_PLAIN=abc\n"
        'COACH_TEST_QUOTED="sk_123 x"\n'
        "export COACH_TEST_EXPORT='y'\n"
        "COACH_TEST_SHELL=from_file\n"
        "not a setting\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("COACH_NO_DOTENV", raising=False)
    for k in ("COACH_TEST_PLAIN", "COACH_TEST_QUOTED", "COACH_TEST_EXPORT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("COACH_TEST_SHELL", "from_shell")

    loaded = load_dotenv(env)

    import os
    assert sorted(loaded) == ["COACH_TEST_EXPORT", "COACH_TEST_PLAIN", "COACH_TEST_QUOTED"]
    assert os.environ["COACH_TEST_PLAIN"] == "abc"
    assert os.environ["COACH_TEST_QUOTED"] == "sk_123 x"
    assert os.environ["COACH_TEST_EXPORT"] == "y"
    assert os.environ["COACH_TEST_SHELL"] == "from_shell"   # shell wins
    for k in loaded:
        monkeypatch.delenv(k)


def test_load_dotenv_off_switch_and_missing_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("COACH_TEST_OFF=1\n", encoding="utf-8")
    monkeypatch.setenv("COACH_NO_DOTENV", "1")
    assert load_dotenv(env) == []
    monkeypatch.delenv("COACH_NO_DOTENV")
    assert load_dotenv(tmp_path / "missing.env") == []
