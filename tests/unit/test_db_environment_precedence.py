import importlib


def test_external_postgres_dsn_wins_over_local_dotenv(monkeypatch):
    import db

    external = "postgresql://external.example:5432/production"
    monkeypatch.setenv("POSTGRES_DSN", external)
    reloaded = importlib.reload(db)

    assert reloaded.POSTGRES_DSN == external

    # Do not let the module-level pool created by another test survive with
    # an environment-specific connection configuration.
    reloaded._pool = None
