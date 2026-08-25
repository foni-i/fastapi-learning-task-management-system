"""Shared pytest fixtures for API tests."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Provide a real application client with a managed lifespan."""

    with TestClient(app) as test_client:
        yield test_client
