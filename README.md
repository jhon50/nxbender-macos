# nxbender-macos

A macOS client for **SonicWall SSL-VPN** (NetExtender protocol) — a drop-in
replacement for the SonicWall *Mobile Connect* app, which on macOS can't save
your password and makes you retype it every connect.

This is a macOS port of [abrasive/nxBender](https://github.com/abrasive/nxBender)
(a Python NetExtender client for Linux). It adds everything needed to run on a
modern Mac, plus a passwordless-sudo launcher and an optional menu-bar toggle.

You connect with **one click and your OTP** — no macOS password, no VPN password
typed, ever.

> SonicWall Mobile Connect is closed-source and the protocol is undocumented;
> this works because the NetExtender protocol was reverse-engineered by the
> upstream project. Tested against SonicWall firewall / SMA-100 class appliances
> (`Server: SonicWALL SSLVPN Web Server`). SMA-1000 (Aventail) is a different
> protocol and is **not** supported.

## Why not openconnect?

openconnect does **not** speak the SonicWall protocol — its `nc` option is
Juniper Network Connect, not NetExtender. It will connect at TLS and then fail
authentication. This project is the right tool for SonicWall on macOS.

## What it does

- Logs in (username / password / domain), including **two-factor**:
  - `X-NE-tf=1` TOTP / authenticator-app ("bind app") codes
  - `X-NE-tf=5` RADIUS challenge
- Brings up a `pppd` tunnel over TLS with the server's routes and DNS
- **Full-tunnel** routing so your public egress is the VPN's IP (required for
  services that allowlist the corporate egress address)
- **Scoped DNS** via `/etc/resolver/<suffix>` so internal hostnames resolve
  without hijacking all of your DNS
- Pins the server certificate by SHA-1 fingerprint

## Requirements

- macOS (Apple Silicon or Intel)
- Xcode Command Line Tools (`xcode-select --install`) — provides `/usr/bin/python3`
- `pppd` (ships with macOS)

## Install

```bash
git clone https://github.com/jhon50/nxbender-macos.git
cd nxbender-macos
./install.sh
```

The installer asks for your VPN **server**, **username**, **domain**, and
**password**, fetches the server's cert fingerprint, then (prompting for your
macOS password once) builds a **root-owned** runtime and a scoped passwordless-sudo
launcher. Don't run it with `sudo` — it elevates itself when needed.

Don't know your **domain**? It's the case-sensitive value in your Mobile Connect
connection config.

## Usage

```bash
~/bin/vpn-connect     # pops a dialog for your OTP, then connects
```

Run from a terminal it stays in the foreground (Ctrl+C disconnects). To
disconnect from anywhere:

```bash
sudo -n /usr/local/sbin/vpn-nx-down
```

### Menu-bar toggle (optional)

```bash
brew install --cask swiftbar
mkdir -p ~/Documents/swiftbar
cp extras/vpn.5s.sh ~/Documents/swiftbar/
```

Point SwiftBar at `~/Documents/swiftbar`. You get a 🔓/🔒 icon with
**Connect** (OTP dialog) / **Disconnect** items.

## Security model

Because the launcher runs as root with passwordless sudo, the **entire exec
chain is root-owned and non-user-writable**, so a process running as you can't
swap any part of it to escalate:

- runtime installed to `/usr/local/lib/nxbender-vpn`, owned `root:wheel`
- the venv is built on the SIP-protected `/usr/bin/python3` (not Homebrew's
  user-writable Python)
- `sudo` is scoped via `/etc/sudoers.d/nxbender-vpn` to exactly two fixed,
  no-argument wrappers (`vpn-nx`, `vpn-nx-down`)
- your VPN password lives in `/etc/nxbender` (mode `600`, root-only)

The only thing you ever type after install is the OTP (it's time-based and can't
be stored — that's the point).

## Uninstall

```bash
./extras/uninstall.sh
```

## Credits

- Upstream protocol implementation: [James Laird-Wah / nxBender](https://github.com/abrasive/nxBender)
- macOS port, 2FA (tf=1), full-tunnel, scoped DNS, root-owned install, menu-bar toggle

## License

BSD 2-Clause (see `LICENSE`). Upstream copyright retained.
