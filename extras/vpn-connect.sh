#!/bin/bash
# Pops a GUI dialog for the VPN OTP, then brings the tunnel up.
# Run from a terminal (foreground, Ctrl+C disconnects) or from a menu-bar app
# like SwiftBar (detached, logs to /tmp/nxbender-vpn.log).
export PATH=/usr/bin:/bin:/usr/sbin:/sbin:$PATH

OTP=$(osascript \
  -e 'tell application "System Events" to display dialog "VPN\n\nEnter the 6-digit code from your authenticator app:" default answer "" with title "VPN" default button 1' \
  -e 'text returned of result' 2>/dev/null || true)

[ -z "${OTP:-}" ] && exit 0

if [ -t 1 ]; then
  printf '%s\n' "$OTP" | sudo -n /usr/local/sbin/vpn-nx
else
  ( printf '%s\n' "$OTP" | nohup sudo -n /usr/local/sbin/vpn-nx >/tmp/nxbender-vpn.log 2>&1 & ) &
fi
