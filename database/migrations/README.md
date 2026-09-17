# Database migrations

Run `alembic upgrade head` to create or update application-owned tables. The
database URL comes from `APP_DATABASE_URL`, then `DATABASE_URL`, and otherwise
falls back to the local `tripmate.db` SQLite file.
