"""Optional persistence — Postgres (audit log, decisions, model registry) and
Redis (snapshot pub/sub, what-if cache). Everything degrades gracefully: if a
service is unreachable the backend runs fully in-memory."""
