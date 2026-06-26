# AeroOps Synthetic Database

This directory contains the synthetic SQLite database file `aeroops.db` used by the AeroOps application.

## Key Information

- **Synthetic Data**: The file contains entirely synthetic demonstration data. No user, company, or proprietary data is present in this database.
- **Regeneration**: This database can be fully regenerated and re-seeded from the code at any time by running:
  ```bash
  uv run aeroops-init-db --reset --db-path data/aeroops.db
  ```
- **Read-Only Access**: The Model Context Protocol (MCP) server connects to and queries this database in strict read-only mode to prevent any modification of operational data during investigations.
