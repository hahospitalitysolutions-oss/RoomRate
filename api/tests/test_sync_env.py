"""scripts/sync_env.py: which settings it adds to .env, and which it leaves alone."""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sync_env.py"
_spec = importlib.util.spec_from_file_location("sync_env", SCRIPT)
sync_env = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sync_env
_spec.loader.exec_module(sync_env)

EXAMPLE = """\
# comment
APIFY_TOKEN=
DATABASE_URL=
APP_ENV=development
SUPABASE_JWT_AUDIENCE=authenticated
ANTHROPIC_API_KEY=
ANTHROPIC_TIMEOUT_SECONDS=75.0
NEON_AUTH_URL=https://example.neonauth.test/neondb/auth
"""


def test_only_missing_settings_with_an_example_value_are_added():
    env = "ANTHROPIC_API_KEY=sk-secret\nAPP_ENV=production\n"

    added = sync_env.missing_settings(EXAMPLE, env)

    # APP_ENV is kept as the owner set it; empty examples would override code
    # defaults; the Supabase settings stay out after the move to Neon Auth.
    assert added == [
        ("ANTHROPIC_TIMEOUT_SECONDS", "75.0"),
        ("NEON_AUTH_URL", "https://example.neonauth.test/neondb/auth"),
    ]


def test_required_settings_left_empty_are_named():
    env = "DATABASE_URL=\nAPIFY_TOKEN=tok\nANTHROPIC_API_KEY=sk\n"

    assert sync_env.unset_required(env) == ["DATABASE_URL", "NEON_AUTH_URL"]


def test_comments_quotes_and_export_lines_parse():
    env = '# DATABASE_URL=commented\nexport APIFY_TOKEN="tok"\nNEON_AUTH_URL = https://x\n'

    assert sync_env.parse_env(env) == {"APIFY_TOKEN": "tok", "NEON_AUTH_URL": "https://x"}


def test_apply_appends_after_a_last_line_without_newline(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env.example").write_text(EXAMPLE, encoding="utf-8")
    # A BOM and no trailing newline, as a Windows editor saves it.
    (tmp_path / ".env").write_text("﻿ANTHROPIC_API_KEY=sk-secret", encoding="utf-8")
    monkeypatch.setattr(sync_env, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["sync_env.py"])

    sync_env.main()

    text = (tmp_path / ".env").read_text(encoding="utf-8-sig")
    assert sync_env.parse_env(text)["ANTHROPIC_API_KEY"] == "sk-secret"
    assert sync_env.parse_env(text)["ANTHROPIC_TIMEOUT_SECONDS"] == "75.0"
    assert (tmp_path / ".env.bak").read_text(encoding="utf-8-sig") == "ANTHROPIC_API_KEY=sk-secret"
    output = capsys.readouterr().out
    assert "sk-secret" not in output  # values are never printed
    assert "DATABASE_URL" in output and "APIFY_TOKEN" in output
