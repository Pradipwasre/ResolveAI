#!/bin/sh
# Start the background worker. Pass --poll to read Telegram without a public webhook (local use).
set -e
alembic upgrade head
exec python -m app.worker "$@"
