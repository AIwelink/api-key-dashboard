# Backend Modules

Backend business logic is organized by feature module here. Keep new feature code in the closest module package instead of adding more modules to `app.services`.

- `accounts`: local account inventory, import parsing, operation records, and pool lifecycle.
- `api_pools`: API pool settings, capacity limits, and status preferences.
- `sub2api`: remote sub2api client, cache, push, return/delete, verify, dashboard, refill, and probe workflows.
- `events`: operational event records and account timelines.
- `notifications`: notification channels, delivery, and batching.
- `agent`: agent-facing analysis, planning, capabilities, and tools.
- `todo`: operational todo workflows.
- `system`: audit, API tokens, and bootstrap/index setup.

`app.services` is kept as a compatibility layer only. New imports should target `app.modules.*` directly.
