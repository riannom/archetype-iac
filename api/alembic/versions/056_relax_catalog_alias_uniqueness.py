"""Allow alias tokens to map to multiple canonical device types.

Revision ID: 056
Revises: 055
Create Date: 2026-02-25
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "056"
down_revision: Union[str, None] = "055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_catalog_device_alias", "catalog_device_aliases", type_="unique")
    op.create_unique_constraint(
        "uq_catalog_device_alias_pair",
        "catalog_device_aliases",
        ["alias", "device_type_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_catalog_device_alias_pair", "catalog_device_aliases", type_="unique")
    op.create_unique_constraint("uq_catalog_device_alias", "catalog_device_aliases", ["alias"])
