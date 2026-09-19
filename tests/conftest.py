from pathlib import Path

import pytest

from scraper.fixture import build_fixture_db


@pytest.fixture(scope="session")
def fixture_data_dir(tmp_path_factory) -> Path:
    """A DATA_DIR holding a published database scored from tests/fixtures/crawl (the Fancy Pants series)."""
    data_dir = tmp_path_factory.mktemp("data")
    build_fixture_db(data_dir, version="test")
    return data_dir
