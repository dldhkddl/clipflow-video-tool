from clipflow.config import Settings
from clipflow.db import Database


def test_settings_stay_local_and_keep_secrets_out_of_repr(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    monkeypatch.setenv("PEXELS_API_KEY", "pexels-secret")
    settings = Settings.load(tmp_path)
    assert settings.host == "127.0.0.1"
    assert settings.monthly_warning_krw == 50_000
    assert settings.monthly_limit_krw == 100_000
    assert settings.pexels_api_key.get_secret_value() == "pexels-secret"
    assert "secret-value" not in repr(settings)
    assert "pexels-secret" not in repr(settings)


def test_database_initialize_is_idempotent(tmp_path):
    db = Database(tmp_path / "clipflow.sqlite3")
    db.initialize()
    db.initialize()
    with db.connection() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"projects", "assets", "scripts", "jobs", "cost_entries"} <= tables
