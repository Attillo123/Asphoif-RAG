#!/bin/sh
set -eu

poll_seconds="${WORKER_POLL_SECONDS:-5}"
echo "Starting ingestion worker (poll=${poll_seconds}s)"

while :; do
    if ! python -m app.cli process-next-ingestion-job; then
        echo "Worker iteration failed; retrying in ${poll_seconds}s" >&2
    fi
    sleep "${poll_seconds}"
done
