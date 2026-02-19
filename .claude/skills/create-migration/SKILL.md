---
name: create-migration
description: Create and validate an Alembic database migration
disable-model-invocation: true
---

# Create Migration

Generate a new Alembic migration for database schema changes.

## Arguments

- `description` (required): Short description for the migration (e.g., "add image checksum column")

## Steps

1. Verify there are pending model changes by checking `api/app/models.py` for recent modifications

2. Generate the migration:
   ```bash
   cd api && alembic revision --autogenerate -m "<description>"
   ```

3. Read the generated migration file and review it for correctness:
   - Verify `upgrade()` contains the expected DDL operations
   - Verify `downgrade()` properly reverses all changes
   - Check for any autogenerate false positives (e.g., index ordering changes)
   - Remove any no-op operations

4. Show the migration to the user for approval before proceeding

5. Test the migration applies cleanly:
   ```bash
   cd api && alembic upgrade head
   ```

6. Report success and the migration file path

## Conventions

- Migration descriptions use lowercase, no period (e.g., "add node placement host_id column")
- One migration per logical change — don't combine unrelated schema changes
- Always verify downgrade path works
