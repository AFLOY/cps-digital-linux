#!/bin/sh
# Install cps-digital-linux for the current user:
#   - udev rule so the logged-in user can open the device (needs sudo once)
#   - ~/.local/bin/cps-digital
#   - systemd user service, enabled and started
#
# Usage: ./install.sh [daemon options...]     e.g. ./install.sh -F
# Options are saved to ~/.config/cps-digital.conf and can be edited later.
set -eu

cd "$(dirname "$0")"
RULE=70-cps-digital.rules
RULE_DST=/etc/udev/rules.d/$RULE
BIN=$HOME/.local/bin/cps-digital
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
CONF=${XDG_CONFIG_HOME:-$HOME/.config}/cps-digital.conf

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemd is required" >&2; exit 1; }
if [ "$(id -u)" = 0 ]; then
    echo "run as your normal user (sudo is used only for the udev rule)" >&2
    exit 1
fi

echo "==> udev rule"
if cmp -s "$RULE" "$RULE_DST"; then
    echo "    $RULE_DST is up to date"
else
    sudo install -m 644 "$RULE" "$RULE_DST"
    sudo udevadm control --reload
    sudo udevadm trigger --subsystem-match=hidraw --action=add
    sudo udevadm settle
    echo "    installed $RULE_DST"
fi

echo "==> program"
install -D -m 755 cps_digital.py "$BIN"
echo "    installed $BIN"

echo "==> options"
if [ $# -gt 0 ] || [ ! -f "$CONF" ]; then
    mkdir -p "$(dirname "$CONF")"
    printf 'CPS_DIGITAL_OPTS="%s"\n' "$*" > "$CONF"
fi
echo "    $CONF: $(cat "$CONF")"

echo "==> systemd user service"
install -D -m 644 cps-digital.service "$UNIT_DIR/cps-digital.service"
systemctl --user daemon-reload
systemctl --user enable cps-digital.service >/dev/null
systemctl --user restart cps-digital.service
sleep 2
if systemctl --user is-active --quiet cps-digital.service; then
    echo "    cps-digital.service is running"
else
    echo "    cps-digital.service is not running yet (is the display plugged in?)"
    echo "    check: systemctl --user status cps-digital"
fi

"$BIN" --list || echo "    no CPS-Digital device found"
