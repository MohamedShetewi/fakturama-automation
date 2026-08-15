"""The .env loader. The failure it exists to prevent is a key that looks
loaded but isn't - a stray quote or BOM producing a 401 far from here.
"""

from __future__ import annotations

import os

from extraction import env


def test_parses_keys_comments_and_blanks():
    values = env.parse("# a comment\n\nOPENAI_API_KEY=sk-abc\nEXTRACT_MODEL = terra\n")
    assert values == {"OPENAI_API_KEY": "sk-abc", "EXTRACT_MODEL": "terra"}


def test_strips_matching_quotes_but_keeps_inner_ones():
    assert env.parse('K="sk-abc"')["K"] == "sk-abc"
    assert env.parse("K='sk-abc'")["K"] == "sk-abc"
    assert env.parse('K=sk-"abc"')["K"] == 'sk-"abc"'


def test_tolerates_export_prefix_and_ignores_junk_lines():
    values = env.parse("export K=v\nnot a pair\n=novalue\n")
    assert values == {"K": "v"}


def test_value_may_contain_equals():
    assert env.parse("K=a=b=c")["K"] == "a=b=c"


def test_load_sets_missing_keys_and_reports_them(tmp_path, monkeypatch):
    monkeypatch.delenv("FAKTURAMA_TEST_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text("FAKTURAMA_TEST_KEY=from-file\n", encoding="utf-8")

    assert env.load(path) == ["FAKTURAMA_TEST_KEY"]
    assert os.environ["FAKTURAMA_TEST_KEY"] == "from-file"


def test_real_environment_wins_over_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKTURAMA_TEST_KEY", "from-shell")
    path = tmp_path / ".env"
    path.write_text("FAKTURAMA_TEST_KEY=from-file\n", encoding="utf-8")

    assert env.load(path) == []
    assert os.environ["FAKTURAMA_TEST_KEY"] == "from-shell"


def test_bom_does_not_become_part_of_the_key(tmp_path, monkeypatch):
    monkeypatch.delenv("FAKTURAMA_TEST_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text("FAKTURAMA_TEST_KEY=v\n", encoding="utf-8-sig")

    assert env.load(path) == ["FAKTURAMA_TEST_KEY"]


def test_missing_file_is_not_an_error(tmp_path):
    assert env.load(tmp_path / "nope.env") == []
