#!/bin/sh

python_is_usable() {
    "$@" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
        >/dev/null 2>&1
}

if command -v python >/dev/null 2>&1 && python_is_usable python; then
    exec python "$@"
fi

if command -v python3 >/dev/null 2>&1 && python_is_usable python3; then
    exec python3 "$@"
fi

if command -v py >/dev/null 2>&1 && python_is_usable py -3; then
    exec py -3 "$@"
fi

printf '%s\n' 'agentic-vault: Python 3.10 or newer is required' >&2
exit 127
