import inspect

from core.db import get_db


def test_get_db_is_async_generator_dependency():
    assert inspect.isasyncgenfunction(get_db)
