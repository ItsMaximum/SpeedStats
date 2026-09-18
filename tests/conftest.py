from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from scraper.legacy import load_legacy_json
from scraper.score import build_published, configure
from speedstats import paths

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixture_data_dir(tmp_path_factory) -> Path:
    """A DATA_DIR holding a published database built from tests/fixtures/test-runs.json, CURRENT set."""
    data_dir = tmp_path_factory.mktemp("data")
    con = duckdb.connect(str(data_dir / "work.duckdb"))
    configure(con, memory_limit="1GB", threads=2)
    load_legacy_json(con, FIXTURES / "test-runs.json")
    out = build_published(
        con,
        paths.published_path(data_dir, "test"),
        data_version="test",
        scraped_at=datetime(2025, 9, 4, tzinfo=UTC),
        excluded_players=[],
    )
    con.close()
    paths.write_current(data_dir, out)
    return data_dir
