#!/bin/sh
set -eu

exec python -m app.workers.declaration_import
