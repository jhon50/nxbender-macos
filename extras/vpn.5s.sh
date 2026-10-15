#!/bin/bash
# SwiftBar plugin: VPN status + connect/disconnect toggle.
# Install: copy into your SwiftBar plugin folder, keep the ".5s." (refresh every 5s).
export PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:$PATH

CONNECT="$HOME/bin/vpn-connect"
DOWN=/usr/local/sbin/vpn-nx-down

if ifconfig ppp0 2>/dev/null | grep -q 'inet '; then
  echo "🔒 VPN"
  echo "---"
  echo "Connected | color=green"
  eg=$(curl -s -m4 https://api.ipify.org 2>/dev/null)
  echo "Egress: ${eg:-unknown} | font=Menlo size=12"
  echo "---"
  echo "Disconnect | bash=/usr/bin/sudo param1=-n param2=$DOWN terminal=false refresh=true"
else
  echo "🔓 VPN"
  echo "---"
  echo "Disconnected | color=gray"
  echo "---"
  echo "Connect… | bash=$CONNECT terminal=false refresh=true"
fi

echo "---"
echo "Refresh | refresh=true"
