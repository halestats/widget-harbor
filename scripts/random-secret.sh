#!/bin/sh
# Linux kernel supplies fresh random UUIDs on each read. stdout is secret material.
# Avoid optional od, openssl and BusyBox tr character-class support.
set -eu
awk '
    { gsub(/-/, ""); value = value $0 }
    END {
        if (length(value) != 64 || value ~ /[^0-9a-f]/) exit 1
        print substr(value, 1, 48)
    }
' /proc/sys/kernel/random/uuid /proc/sys/kernel/random/uuid
