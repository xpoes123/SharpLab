#!/bin/sh
# The nested kwin_wayland's "session": hand our env (its WAYLAND_DISPLAY + the private D-Bus) to the wall service, then
# live as long as kwin should (the service ends the whole process group).
printf 'WAYLAND_DISPLAY=%s\nDBUS_SESSION_BUS_ADDRESS=%s\n' "$WAYLAND_DISPLAY" "$DBUS_SESSION_BUS_ADDRESS" > "$WALL_ENV_OUT.tmp"
mv "$WALL_ENV_OUT.tmp" "$WALL_ENV_OUT"
exec sleep infinity
