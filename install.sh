#!/bin/bash
#
# nxbender-macos installer.
#
# Builds a root-owned nxBender runtime on the SIP-protected system Python,
# stores your VPN config in a root-only file, and sets up a passwordless-sudo
# launcher so day-to-day connects only ever ask for your OTP.
#
# Run as your normal user (NOT with sudo); it will prompt for your password
# once when it needs root.
#
set -euo pipefail

if [ "$(id -u)" = 0 ]; then
  echo "Run this as your normal user, not with sudo. It will sudo when needed." >&2
  exit 1
fi

export PATH=/usr/bin:/bin:/usr/sbin:/sbin:$PATH

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR=/usr/local/lib/nxbender-vpn
WRAPPER=/usr/local/sbin/vpn-nx
WRAPPER_DOWN=/usr/local/sbin/vpn-nx-down
CONFIG=/etc/nxbender
SUDOERS=/etc/sudoers.d/nxbender-vpn
USER_LAUNCHER="$HOME/bin/vpn-connect"
ME="$(id -un)"

echo "nxbender-macos installer"
echo "========================"

# --- gather config ---
read -r -p "VPN server host (e.g. vpn.example.com): " SERVER
read -r -p "Username: " USERNAME
read -r -p "Login domain (case-sensitive; from your Mobile Connect config): " DOMAIN
read -r -s -p "Password: " PASSWORD; echo
echo

# --- discover the server's SHA1 cert fingerprint for pinning ---
echo "Fetching server certificate fingerprint..."
FP=$(echo | openssl s_client -connect "$SERVER:443" -servername "$SERVER" 2>/dev/null \
     | openssl x509 -noout -fingerprint -sha1 2>/dev/null | sed 's/.*=//' | tr 'A-F' 'a-f')
if [ -z "$FP" ]; then
  echo "Could not fetch certificate fingerprint from $SERVER:443" >&2
  exit 1
fi
echo "  fingerprint: $FP"

# --- pick a system python (SIP/root-owned, so the root venv can't be tampered) ---
PY=/usr/bin/python3
"$PY" -c 'import venv, ssl, ipaddress' 2>/dev/null || {
  echo "$PY lacks venv/ssl; install the Command Line Tools (xcode-select --install)" >&2
  exit 1
}

echo
echo "The next steps need root. You'll be prompted for your password once."
sudo -v

# --- build the root-owned runtime ---
sudo bash -s -- "$REPO_DIR" "$INSTALL_DIR" "$PY" <<'ROOT'
set -euo pipefail
REPO_DIR="$1"; INSTALL_DIR="$2"; PY="$3"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/src"
cp -R "$REPO_DIR/nxbender" "$REPO_DIR/setup.py" "$REPO_DIR/requirements.txt" "$INSTALL_DIR/src/"
"$PY" -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" -q install --upgrade pip >/dev/null 2>&1 || true
"$INSTALL_DIR/venv/bin/pip" -q install "$INSTALL_DIR/src"
chown -R root:wheel "$INSTALL_DIR"
chmod -R go-w "$INSTALL_DIR"
ROOT

NX="$INSTALL_DIR/venv/bin/nxBender"

# --- write root-only config (password lives here, 600 root) ---
printf 'password = %s\n' "$PASSWORD" | sudo tee "$CONFIG" >/dev/null
sudo chown root:wheel "$CONFIG"
sudo chmod 600 "$CONFIG"

# --- write the fixed, no-arg root launcher (prevents arg injection under NOPASSWD) ---
TMPW=$(mktemp)
cat > "$TMPW" <<WRAP
#!/bin/bash
# Fixed-config SonicWall VPN launcher. No arguments honoured. Root-owned.
exec "$NX" \\
  -c "$CONFIG" \\
  -s "$SERVER" \\
  -u "$USERNAME" \\
  -d "$DOMAIN" \\
  -f "$FP" \\
  --pinentry none \\
  --show-ppp-log
WRAP
sudo install -o root -g wheel -m 755 "$TMPW" "$WRAPPER"
rm -f "$TMPW"

TMPD=$(mktemp)
cat > "$TMPD" <<DOWN
#!/bin/bash
# Gracefully tear down the VPN. No args. Root-owned.
NX='$NX'
pkill -INT -f "\$NX" 2>/dev/null
sleep 2
pkill -KILL -f "\$NX" 2>/dev/null
pkill -KILL -f 'pppd debug debug dump logfd 2' 2>/dev/null
exit 0
DOWN
sudo install -o root -g wheel -m 755 "$TMPD" "$WRAPPER_DOWN"
rm -f "$TMPD"

# --- NOPASSWD sudo, scoped to just these two root-owned wrappers ---
echo "$ME ALL=(root) NOPASSWD: $WRAPPER, $WRAPPER_DOWN" | sudo tee "$SUDOERS" >/dev/null
sudo chmod 440 "$SUDOERS"
sudo visudo -c -f "$SUDOERS" >/dev/null

# --- user launcher: pops an OTP dialog then connects ---
mkdir -p "$HOME/bin"
cp "$REPO_DIR/extras/vpn-connect.sh" "$USER_LAUNCHER"
chmod +x "$USER_LAUNCHER"

echo
echo "Installed."
echo "  Connect:    $USER_LAUNCHER   (or run it from a terminal; Ctrl+C disconnects)"
echo "  Disconnect: sudo -n $WRAPPER_DOWN"
echo
echo "Optional menu-bar toggle (SwiftBar):"
echo "  brew install --cask swiftbar"
echo "  mkdir -p ~/Documents/swiftbar && cp $REPO_DIR/extras/vpn.5s.sh ~/Documents/swiftbar/"
echo "  then point SwiftBar at ~/Documents/swiftbar"
