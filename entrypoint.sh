#!/bin/sh
set -e

# Default to nobody:users (Unraid standard) if PUID/PGID not set
PUID="${PUID:-99}"
PGID="${PGID:-100}"

# Ensure the app directories exist and have correct ownership
# This fixes the root-owned dirs created by Docker on first run
for dir in /app/config /app/data; do
    mkdir -p "$dir"
    chown "$PUID:$PGID" "$dir"
    chmod 755 "$dir"
done

# Fix ownership of existing files. A share that refuses chown (NFS with
# root_squash, some SMB mounts) is survivable, but say so: the bot may then
# be unable to save mutes and ignores.
for dir in /app/data /app/config; do
    chown -R "$PUID:$PGID" "$dir" || echo "entrypoint: warning: could not chown $dir to $PUID:$PGID" >&2
done

# Update appuser UID/GID to match PUID/PGID. A failure here used to be
# swallowed, and the bot then ran as the wrong user and could not write its
# own files; stop with the reason instead.
if [ "$(id -u appuser)" != "$PUID" ] && ! usermod -o -u "$PUID" appuser; then
    echo "entrypoint: could not set appuser UID to $PUID" >&2
    exit 1
fi
if [ "$(id -g appuser)" != "$PGID" ] && ! groupmod -o -g "$PGID" appuser; then
    echo "entrypoint: could not set appuser GID to $PGID" >&2
    exit 1
fi

# Join the group that owns the Docker socket on *this* host. The image bakes
# in GID 281 (Unraid's docker group); Ubuntu uses 999, Docker Desktop root.
# Reading it at start means the published image works everywhere.
SOCK=/var/run/docker.sock
if [ -S "$SOCK" ]; then
    SOCK_GID="$(stat -c %g "$SOCK")"
    SOCK_GROUP="$(getent group "$SOCK_GID" | cut -d: -f1)"
    if [ -z "$SOCK_GROUP" ]; then
        SOCK_GROUP=dockersock-host
        groupadd -o -g "$SOCK_GID" "$SOCK_GROUP"
    fi
    if ! id -nG appuser | tr ' ' '\n' | grep -qx "$SOCK_GROUP"; then
        usermod -aG "$SOCK_GROUP" appuser
    fi
    echo "entrypoint: Docker socket group is $SOCK_GROUP ($SOCK_GID)"
else
    echo "entrypoint: no Docker socket at $SOCK; container monitoring stays off until one is mounted" >&2
fi

# Set permissive umask so created files (config.yaml, mute/ignore JSON)
# are readable/writable by group and others (ownership handles access control)
umask 0022

# Drop privileges and run as appuser
exec gosu appuser "$@"
