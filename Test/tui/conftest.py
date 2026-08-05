# 01.08.26

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
