"""Integration test configuration.

Session-scoped fixture that ensures the Azurite blob container exists and
sets STORE_URL / STORE_CONTAINER defaults so tests work out of the box
after `docker compose up -d` with no manual setup step.
"""

from __future__ import annotations

import os

import pytest

_AZURITE_CONN = "UseDevelopmentStorage=true"
_AZURITE_CONTAINER = "integration-showcase"


@pytest.fixture(scope="session", autouse=True)
def azurite_container() -> None:
    """Create the Azurite blob container; skip the session if Azurite is unreachable."""
    from azure.core.exceptions import ResourceExistsError, ServiceRequestError
    from azure.storage.blob import BlobServiceClient

    os.environ.setdefault("STORE_URL", _AZURITE_CONN)
    os.environ.setdefault("STORE_CONTAINER", _AZURITE_CONTAINER)

    client = BlobServiceClient.from_connection_string(_AZURITE_CONN)
    try:
        client.create_container(_AZURITE_CONTAINER)
    except ResourceExistsError:
        pass  # already exists — nothing to do
    except ServiceRequestError:
        pytest.skip("Azurite not reachable. Run: docker compose up -d azurite")
