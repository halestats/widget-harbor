#!/bin/sh
# Compatibility entry point. See docs/INSTALL.md and docs/UPGRADE-1.4.md.
exec sh "$(dirname "$0")/upgrade.sh" "$@"
