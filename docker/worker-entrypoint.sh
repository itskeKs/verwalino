#!/bin/sh
set -eu

exec python -m app.workers."${HV_WORKER_KIND:-declaration_import}" "$@"
