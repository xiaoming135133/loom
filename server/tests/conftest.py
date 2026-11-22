from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from server.main import create_app


@pytest.fixture()
def client(tmp_path) -> Iterator[TestClient]:
    app = create_app(tmp_path / "data")
    with TestClient(app) as test_client:
        yield test_client
