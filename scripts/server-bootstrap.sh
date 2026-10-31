#!/usr/bin/env bash
# One-time bootstrap of a fresh Debian/Ubuntu host for the hosted Verwalino server.
#
# Usage (as root, once):  server-bootstrap.sh ADMIN_USER PUBKEY_FILE [HOSTNAME]
#   ADMIN_USER   login created with passwordless sudo and Docker access
#   PUBKEY_FILE  OpenSSH public key file (one or more keys) installed for ADMIN_USER
#   HOSTNAME     optional; the machine keeps its name when omitted
# TIMEZONE selects the system time zone (default Europe/Berlin).
#
# Afterwards root login and password logins are disabled; only ADMIN_USER may log in.
# Safe to repeat. Note: the firewall is reset to exactly the rules below.
# /srv/verwalino and the restricted CI key are set up as described in DEPLOY.md, "Hosted server".
set -euo pipefail

usage() {
    printf 'Usage: %s ADMIN_USER PUBKEY_FILE [HOSTNAME]\n' "${0##*/}" >&2
    exit 2
}
fail() { printf '%s\n' "$1" >&2; exit 1; }

[[ $# -ge 2 && $# -le 3 ]] || usage
ADMIN_USER=$1
PUBKEY_FILE=$2
NEW_HOSTNAME=${3:-}
TIMEZONE=${TIMEZONE:-Europe/Berlin}

[[ "$ADMIN_USER" =~ ^[a-z][a-z0-9_-]{0,30}$ && "$ADMIN_USER" != root ]] \
    || fail 'ADMIN_USER must be a lowercase login name other than root.'
[[ -f "$PUBKEY_FILE" && -s "$PUBKEY_FILE" ]] || fail "PUBKEY_FILE not found or empty: $PUBKEY_FILE"
if [[ -n "$NEW_HOSTNAME" ]]; then
    [[ "$NEW_HOSTNAME" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || fail 'HOSTNAME must be a single lowercase DNS label.'
fi
[[ $EUID -eq 0 ]] || fail 'Run as root.'
command -v apt-get >/dev/null || fail 'An apt-based system (Debian or Ubuntu) is required.'
# shellcheck disable=SC1091
. /etc/os-release
case "$ID" in debian|ubuntu) ;; *) fail "Unsupported distribution: $ID" ;; esac

export DEBIAN_FRONTEND=noninteractive
APT=(apt-get -y -q -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)
echo "== $PRETTY_NAME, $(nproc) vCPU, $(free -g | awk '/Mem/{print $2}') GB RAM"

echo "== packages"
"${APT[@]}" update
"${APT[@]}" upgrade
"${APT[@]}" install sudo ufw fail2ban unattended-upgrades cron curl git ca-certificates gnupg rsync jq openssh-client

echo "== admin user $ADMIN_USER"
ssh-keygen -l -f "$PUBKEY_FILE" >/dev/null || fail 'PUBKEY_FILE does not contain valid OpenSSH public keys.'
id "$ADMIN_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$ADMIN_USER"
SSH_DIR="/home/$ADMIN_USER/.ssh"
install -d -m 700 -o "$ADMIN_USER" -g "$ADMIN_USER" "$SSH_DIR"
touch "$SSH_DIR/authorized_keys"
while IFS= read -r key || [[ -n "$key" ]]; do
    [[ -n "$key" && "$key" != \#* ]] || continue
    grep -qxF -- "$key" "$SSH_DIR/authorized_keys" || printf '%s\n' "$key" >> "$SSH_DIR/authorized_keys"
done < "$PUBKEY_FILE"
chown "$ADMIN_USER:$ADMIN_USER" "$SSH_DIR/authorized_keys"
chmod 600 "$SSH_DIR/authorized_keys"
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$ADMIN_USER" > "/etc/sudoers.d/$ADMIN_USER"
chmod 440 "/etc/sudoers.d/$ADMIN_USER"
visudo -cf "/etc/sudoers.d/$ADMIN_USER" >/dev/null

echo "== sshd hardening"
install -d /etc/ssh/sshd_config.d
{
    cat <<'CONF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
X11Forwarding no
MaxAuthTries 3
LoginGraceTime 30
CONF
    printf 'AllowUsers %s\n' "$ADMIN_USER"
} > /etc/ssh/sshd_config.d/10-hardening.conf
sshd -t
# Fail before the session ends if another drop-in wins over the hardening file.
effective=$(sshd -T)
grep -qx 'passwordauthentication no' <<< "$effective" || fail 'sshd still allows password logins; check /etc/ssh/sshd_config.d.'
grep -qx 'permitrootlogin no' <<< "$effective" || fail 'sshd still allows root logins; check /etc/ssh/sshd_config.d.'
systemctl restart ssh 2>/dev/null || systemctl restart sshd

echo "== firewall"
# Docker publishes ports around ufw: the stack must bind only 80/443 (Caddy) publicly.
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw limit 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "== fail2ban + unattended upgrades"
cat > /etc/fail2ban/jail.d/sshd.local <<'CONF'
[sshd]
enabled = true
maxretry = 4
bantime = 1h
CONF
systemctl enable fail2ban
systemctl restart fail2ban
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
CONF
systemctl enable --now unattended-upgrades

echo "== docker"
if ! command -v docker >/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
    "${APT[@]}" update
    "${APT[@]}" install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
usermod -aG docker "$ADMIN_USER"

echo "== log limits"
# Same rotation as the x-logging block of infra/docker-compose.yml, for every other container.
install -d /etc/docker
daemon_json=$(mktemp)
log_settings='{"log-driver":"local","log-opts":{"max-size":"10m","max-file":"3"}}'
if [[ -s /etc/docker/daemon.json ]]; then
    jq --argjson add "$log_settings" '. + $add' /etc/docker/daemon.json > "$daemon_json"
else
    jq -n --argjson add "$log_settings" '$add' > "$daemon_json"
fi
if ! cmp -s "$daemon_json" /etc/docker/daemon.json; then
    install -m 644 "$daemon_json" /etc/docker/daemon.json
    systemctl restart docker
fi
rm -f "$daemon_json"
systemctl enable --now docker
install -d /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/10-verwalino.conf <<'CONF'
[Journal]
Storage=persistent
SystemMaxUse=500M
SystemKeepFree=1G
MaxRetentionSec=1month
CONF
systemctl restart systemd-journald

echo "== swap"
if [[ -n "$(swapon --noheadings --show=NAME)" ]]; then
    echo "swap already active"
else
    [[ -e /swapfile ]] || {
        dd if=/dev/zero of=/swapfile bs=1M count=4096 status=none
        chmod 600 /swapfile
        mkswap /swapfile >/dev/null
    }
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== time zone and host name"
timedatectl set-timezone "$TIMEZONE"
[[ -z "$NEW_HOSTNAME" ]] || hostnamectl set-hostname "$NEW_HOSTNAME"

echo "== done"
docker --version
docker compose version
ufw status | head -8
echo "Root and password logins are now disabled. Log in as: ssh $ADMIN_USER@$(hostname -I | awk '{print $1}')"
