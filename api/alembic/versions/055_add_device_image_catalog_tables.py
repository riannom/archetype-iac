"""Add canonical device/image catalog tables.

Revision ID: 055
Revises: 054
Create Date: 2026-02-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "055"
down_revision: Union[str, None] = "054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "catalog_vendors",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("vendor_key", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vendor_key", name="uq_catalog_vendor_key"),
    )
    op.create_index("ix_catalog_vendors_vendor_key", "catalog_vendors", ["vendor_key"])

    op.create_table(
        "catalog_device_types",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canonical_device_id", sa.String(length=120), nullable=False),
        sa.Column("vendor_id", sa.String(length=36), nullable=False),
        sa.Column("runtime_kind", sa.String(length=120), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("device_class", sa.String(length=50), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False, server_default="builtin"),
        sa.Column("lifecycle_status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("eol_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["vendor_id"], ["catalog_vendors.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_device_id", name="uq_catalog_device_canonical_id"),
    )
    op.create_index(
        "ix_catalog_device_types_canonical_device_id",
        "catalog_device_types",
        ["canonical_device_id"],
    )
    op.create_index("ix_catalog_device_types_vendor_id", "catalog_device_types", ["vendor_id"])

    op.create_table(
        "catalog_device_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("device_type_id", sa.String(length=36), nullable=False),
        sa.Column("version_tag", sa.String(length=120), nullable=False, server_default="current"),
        sa.Column("runtime_kind", sa.String(length=120), nullable=True),
        sa.Column("memory_mb", sa.Integer(), nullable=True),
        sa.Column("cpu_count", sa.Integer(), nullable=True),
        sa.Column("max_ports", sa.Integer(), nullable=True),
        sa.Column("supported_image_kinds_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("valid_from", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["device_type_id"], ["catalog_device_types.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_type_id", "version_tag", name="uq_catalog_device_revision_version"),
    )
    op.create_index("ix_catalog_device_revisions_device_type_id", "catalog_device_revisions", ["device_type_id"])
    op.create_index("ix_catalog_device_revisions_is_current", "catalog_device_revisions", ["is_current"])

    op.create_table(
        "catalog_device_aliases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("device_type_id", sa.String(length=36), nullable=False),
        sa.Column("alias", sa.String(length=120), nullable=False),
        sa.Column("alias_type", sa.String(length=40), nullable=False, server_default="explicit"),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="seed"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["device_type_id"], ["catalog_device_types.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alias", name="uq_catalog_device_alias"),
    )
    op.create_index("ix_catalog_device_aliases_alias", "catalog_device_aliases", ["alias"])
    op.create_index("ix_catalog_device_aliases_device_type_id", "catalog_device_aliases", ["device_type_id"])

    op.create_table(
        "catalog_images",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("reference", sa.String(length=500), nullable=True),
        sa.Column("filename", sa.String(length=500), nullable=True),
        sa.Column("digest_sha256", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("vendor_name", sa.String(length=200), nullable=True),
        sa.Column("version", sa.String(length=120), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="manifest"),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id", name="uq_catalog_image_external_id"),
    )
    op.create_index("ix_catalog_images_external_id", "catalog_images", ["external_id"])

    op.create_table(
        "catalog_image_device_compat",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("image_id", sa.String(length=36), nullable=False),
        sa.Column("device_type_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="manifest"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["device_type_id"], ["catalog_device_types.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["image_id"], ["catalog_images.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("image_id", "device_type_id", name="uq_catalog_image_device_compat"),
    )
    op.create_index("ix_catalog_image_device_compat_image_id", "catalog_image_device_compat", ["image_id"])
    op.create_index("ix_catalog_image_device_compat_device_type_id", "catalog_image_device_compat", ["device_type_id"])

    op.create_table(
        "catalog_image_defaults",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("device_type_id", sa.String(length=36), nullable=False),
        sa.Column("image_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="manifest"),
        sa.Column("set_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["device_type_id"], ["catalog_device_types.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["image_id"], ["catalog_images.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_type_id", name="uq_catalog_image_default_device"),
    )
    op.create_index("ix_catalog_image_defaults_device_type_id", "catalog_image_defaults", ["device_type_id"])
    op.create_index("ix_catalog_image_defaults_image_id", "catalog_image_defaults", ["image_id"])

    op.create_table(
        "catalog_ingest_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_catalog_ingest_events_source", "catalog_ingest_events", ["source"])
    op.create_index("ix_catalog_ingest_events_event_type", "catalog_ingest_events", ["event_type"])
    op.create_index("ix_catalog_ingest_events_created_at", "catalog_ingest_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_catalog_ingest_events_created_at", table_name="catalog_ingest_events")
    op.drop_index("ix_catalog_ingest_events_event_type", table_name="catalog_ingest_events")
    op.drop_index("ix_catalog_ingest_events_source", table_name="catalog_ingest_events")
    op.drop_table("catalog_ingest_events")

    op.drop_index("ix_catalog_image_defaults_image_id", table_name="catalog_image_defaults")
    op.drop_index("ix_catalog_image_defaults_device_type_id", table_name="catalog_image_defaults")
    op.drop_table("catalog_image_defaults")

    op.drop_index("ix_catalog_image_device_compat_device_type_id", table_name="catalog_image_device_compat")
    op.drop_index("ix_catalog_image_device_compat_image_id", table_name="catalog_image_device_compat")
    op.drop_table("catalog_image_device_compat")

    op.drop_index("ix_catalog_images_external_id", table_name="catalog_images")
    op.drop_table("catalog_images")

    op.drop_index("ix_catalog_device_aliases_device_type_id", table_name="catalog_device_aliases")
    op.drop_index("ix_catalog_device_aliases_alias", table_name="catalog_device_aliases")
    op.drop_table("catalog_device_aliases")

    op.drop_index("ix_catalog_device_revisions_is_current", table_name="catalog_device_revisions")
    op.drop_index("ix_catalog_device_revisions_device_type_id", table_name="catalog_device_revisions")
    op.drop_table("catalog_device_revisions")

    op.drop_index("ix_catalog_device_types_vendor_id", table_name="catalog_device_types")
    op.drop_index("ix_catalog_device_types_canonical_device_id", table_name="catalog_device_types")
    op.drop_table("catalog_device_types")

    op.drop_index("ix_catalog_vendors_vendor_key", table_name="catalog_vendors")
    op.drop_table("catalog_vendors")
