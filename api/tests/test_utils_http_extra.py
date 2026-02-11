from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.utils.http import require_lab_owner


def test_require_lab_owner_raises_without_db(test_user, sample_lab):
    with pytest.raises(ValueError):
        require_lab_owner(test_user, sample_lab)


def test_require_lab_owner_raises_for_missing_role(test_user, sample_lab, test_db):
    with pytest.raises(HTTPException):
        require_lab_owner(test_user, sample_lab, db=test_db)
