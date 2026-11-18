from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

# DATA_DIR must be set before app.config is imported anywhere.
_TMP = tempfile.mkdtemp(prefix="vouchpin-test-")
os.environ["DATA_DIR"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import DB_PATH  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402


@pytest.fixture()
def client():
    Path(DB_PATH).unlink(missing_ok=True)
    init_db()
    seed_if_empty()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def day(client):
    return client.get("/api/settings/day").json()["business_day"]
