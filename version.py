"""
Centralized version tags for DB-created artifacts.

Increment these when changing:
- DB_SCHEMA_VERSION: DB schema structure (columns, tables)
- DEVICE_CREATOR_VERSION: device-generation logic / physics parameters encoded in DB
- TASK_CREATOR_VERSION: task-generation logic / task semantics encoded in DB
"""

# Keep these as simple strings to embed into DB rows.
DB_SCHEMA_VERSION = "1"
DEVICE_CREATOR_VERSION = "1"
TASK_CREATOR_VERSION = "1"

