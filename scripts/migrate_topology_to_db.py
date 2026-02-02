#!/usr/bin/env python3
"""Migrate existing lab topologies from YAML files to database.

This script performs a one-time migration of topology data from YAML files
in lab workspaces to the new Node and Link database tables.

After running this migration, the database becomes the source of truth
for topology structure. YAML files are kept as backups.

Usage:
    cd api
    python ../scripts/migrate_topology_to_db.py

Or with explicit database URL:
    DATABASE_URL=postgresql://... python ../scripts/migrate_topology_to_db.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Add api directory to path for imports
api_dir = Path(__file__).parent.parent / "api"
sys.path.insert(0, str(api_dir))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.config import settings
from app.services.topology import TopologyService
from app.storage import topology_path, lab_workspace

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def migrate_lab(session: Session, lab: models.Lab) -> tuple[int, int]:
    """Migrate a single lab's topology from YAML to database.

    Args:
        session: Database session
        lab: Lab model instance

    Returns:
        Tuple of (nodes_created, links_created)
    """
    # Check if lab already has nodes in database
    existing_nodes = (
        session.query(models.Node)
        .filter(models.Node.lab_id == lab.id)
        .count()
    )
    if existing_nodes > 0:
        logger.info(f"Lab {lab.id} ({lab.name}) already has {existing_nodes} nodes in DB, skipping")
        return (0, 0)

    # Read topology YAML file
    topo_path = topology_path(lab.id)
    if not topo_path.exists():
        logger.debug(f"Lab {lab.id} ({lab.name}) has no topology.yml file")
        return (0, 0)

    try:
        yaml_content = topo_path.read_text(encoding="utf-8")
        if not yaml_content.strip():
            logger.debug(f"Lab {lab.id} ({lab.name}) has empty topology.yml")
            return (0, 0)
    except Exception as e:
        logger.error(f"Failed to read topology.yml for lab {lab.id}: {e}")
        return (0, 0)

    # Use TopologyService to import
    service = TopologyService(session)
    try:
        nodes_created, links_created = service.update_from_yaml(lab.id, yaml_content)
        logger.info(
            f"Migrated lab {lab.id} ({lab.name}): "
            f"{nodes_created} nodes, {links_created} links"
        )
        return (nodes_created, links_created)
    except Exception as e:
        logger.error(f"Failed to migrate lab {lab.id} ({lab.name}): {e}")
        return (0, 0)


def main():
    """Main migration function."""
    logger.info("Starting topology migration from YAML to database")
    logger.info(f"Database URL: {settings.database_url[:50]}...")

    # Create database engine and session
    engine = create_engine(settings.database_url)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    try:
        # Get all labs
        labs = session.query(models.Lab).all()
        logger.info(f"Found {len(labs)} labs to process")

        total_nodes = 0
        total_links = 0
        migrated_count = 0
        skipped_count = 0
        error_count = 0

        for lab in labs:
            try:
                nodes, links = migrate_lab(session, lab)
                if nodes > 0 or links > 0:
                    migrated_count += 1
                    total_nodes += nodes
                    total_links += links
                else:
                    skipped_count += 1
            except Exception as e:
                logger.error(f"Error processing lab {lab.id}: {e}")
                error_count += 1
                session.rollback()
                continue

            # Commit after each lab to avoid losing progress
            session.commit()

        logger.info("=" * 60)
        logger.info("Migration complete!")
        logger.info(f"  Labs migrated: {migrated_count}")
        logger.info(f"  Labs skipped: {skipped_count}")
        logger.info(f"  Labs with errors: {error_count}")
        logger.info(f"  Total nodes created: {total_nodes}")
        logger.info(f"  Total links created: {total_links}")

    finally:
        session.close()


if __name__ == "__main__":
    main()
