#!/bin/sh
# Drop-privilege entrypoint (linuxserver-style PUID/PGID).
#
# Runs as root: fix ownership of the mounted XDG directories (and the
# editable egg-info, which uv run touches at startup) only when the top
# dir is not already owned by the target user -- chown -R on a warm
# cache would otherwise add seconds to every start. Then exec the
# command as that user via setpriv.
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}

maybe_chown() {
    d=$1
    [ -d "$d" ] || return 0
    [ "$(stat -c '%u' "$d")" = "$PUID" ] && return 0
    chown -R "$PUID:$PGID" "$d"
}

for d in "$HOME/.config" "$HOME/.local/share" "$HOME/.local/state" "$HOME/.cache"; do
    maybe_chown "$d"
done
maybe_chown /app/src/genesis.egg-info

exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
