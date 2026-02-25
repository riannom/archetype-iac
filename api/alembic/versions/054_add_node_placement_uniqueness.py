"""Deduplicate node placements and enforce one row per lab/node.

Revision ID: 054
Revises: 053
Create Date: 2026-02-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "054"
down_revision: Union[str, None] = "053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keep the best placement per (lab_id, node_name), then enforce uniqueness.
    #
    # Winner ordering:
    # 1) deployed > starting/running > pending > failed/other
    # 2) newest created_at
    # 3) highest id (deterministic tie-breaker)
    op.execute(
        sa.text(
            """
            DELETE FROM node_placements
            WHERE id IN (
                SELECT id
                FROM (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY lab_id, node_name
                            ORDER BY
                                CASE status
                                    WHEN 'deployed' THEN 4
                                    WHEN 'starting' THEN 3
                                    WHEN 'running' THEN 3
                                    WHEN 'pending' THEN 2
                                    WHEN 'failed' THEN 1
                                    ELSE 0
                                END DESC,
                                COALESCE(created_at, CURRENT_TIMESTAMP) DESC,
                                id DESC
                        ) AS rn
                    FROM node_placements
                ) ranked
                WHERE ranked.rn > 1
            )
            """
        )
    )

    op.create_unique_constraint(
        "uq_node_placement_lab_node",
        "node_placements",
        ["lab_id", "node_name"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_node_placement_lab_node",
        "node_placements",
        type_="unique",
    )
