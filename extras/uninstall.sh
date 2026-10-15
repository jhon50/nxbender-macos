#!/bin/bash
# Remove nxbender-macos. Run as your normal user; it will sudo when needed.
set -euo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin:$PATH

sudo -n /usr/local/sbin/vpn-nx-down 2>/dev/null || true
sudo rm -rf /usr/local/lib/nxbender-vpn
sudo rm -f /usr/local/sbin/vpn-nx /usr/local/sbin/vpn-nx.bak /usr/local/sbin/vpn-nx-down
sudo rm -f /etc/nxbender /etc/sudoers.d/nxbender-vpn
rm -f "$HOME/bin/vpn-connect"
echo "Removed. (SwiftBar plugin, if installed, left in place — delete it from your plugin folder.)"
