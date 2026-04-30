"""Pytest fixtures for media_processor tests.

`media_processor.api.config` instantiates a `Settings()` at import time, which
in turn requires `POSTGRES_*` env vars. Setting them in an autouse fixture is
too late: collection-time imports trigger validation before the fixture runs.
We populate the env at conftest *module load* so every test module sees them.
"""

from __future__ import annotations

import os

_DEFAULTS = {
    "POSTGRES_USER": "test",
    "POSTGRES_PASSWORD": "test",
    "POSTGRES_DB": "test",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "REDIS_HOST": "localhost",
    "REDIS_PORT": "6379",
    "API_HOST": "0.0.0.0",
    "API_PORT": "8000",
}

for key, value in _DEFAULTS.items():
    os.environ.setdefault(key, value)
