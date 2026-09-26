import pytest
from typer.testing import CliRunner

from watchpatch.database import Database


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "watchpatch.db"
    monkeypatch.setenv("WATCHPATCH_DB", str(path))
    monkeypatch.setenv("WATCHPATCH_NO_NOTIFY", "1")
    database = Database(path)
    yield database
    database.close()


@pytest.fixture
def runner(db):
    return CliRunner()
