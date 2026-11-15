"""Shared fixtures live in tests/conftest.py (isolated storage, no real keys, as_profile).

Imported explicitly: `import *` skips underscore names, which would silently drop the autouse fixture.
"""
from tests.conftest import _isolated_storage, as_profile  # noqa: F401
