#!/bin/sh
# Remove everything install.sh put in place.
set -eu

UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
CONF=${XDG_CONFIG_HOME:-$HOME/.config}/cps-digital.conf

systemctl --user disable --now cps-digital.service 2>/dev/null || true
rm -f "$UNIT_DIR/cps-digital.service" "$HOME/.local/bin/cps-digital" "$CONF"
systemctl --user daemon-reload

if [ -e /etc/udev/rules.d/70-cps-digital.rules ]; then
    sudo rm -f /etc/udev/rules.d/70-cps-digital.rules
    sudo udevadm control --reload
fi
echo "cps-digital-linux removed"
