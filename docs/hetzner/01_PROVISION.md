# 01 — Server Provisioning (Hetzner CCX33)

**Target**: Hetzner CCX33 — 8 dedicated vCPU (AMD EPYC), 32 GB RAM, 240 GB NVMe
**Location**: Nuremberg (nbg1), Germany
**OS**: Ubuntu 24.04 LTS
**Cost**: EUR 62.99/mo (EUR 62.49 server + EUR 0.50 IPv4, excl. VAT)

## Status: PROVISIONED

| Field | Value |
|-------|-------|
| Server name | `NeoDemos-ubuntu-32gb-nbg1-1` |
| Server ID | `#126129400` |
| IPv4 | `178.104.137.168` |
| IPv6 | `2a01:4f8:1c1c:9e14::/64` |
| Location | Nuremberg (nbg1) |
| Image | Ubuntu 24.04 |

---

## Prerequisites

Install the Hetzner Cloud CLI on your Mac:

```bash
brew install hcloud
```

Create an API token at https://console.hetzner.cloud → Project → Security → API Tokens → Generate (Read & Write).

```bash
hcloud context create neodemos
# Paste your API token when prompted
```

---

## Step 1: SSH Key

```bash
# Generate a dedicated deploy key (if you don't have one)
ssh-keygen -t ed25519 -C "neodemos-deploy" -f ~/.ssh/neodemos_ed25519

# Register it with Hetzner
hcloud ssh-key create --name neodemos-deploy \
  --public-key-from-file ~/.ssh/neodemos_ed25519.pub
```

---

## Step 2: Create Firewall

```bash
hcloud firewall create --name neodemos-fw

# SSH
hcloud firewall add-rule neodemos-fw \
  --direction in --protocol tcp --port 22 \
  --source-ips 0.0.0.0/0 --source-ips ::/0 \
  --description "SSH"

# HTTP (needed for Caddy ACME challenge)
hcloud firewall add-rule neodemos-fw \
  --direction in --protocol tcp --port 80 \
  --source-ips 0.0.0.0/0 --source-ips ::/0 \
  --description "HTTP"

# HTTPS
hcloud firewall add-rule neodemos-fw \
  --direction in --protocol tcp --port 443 \
  --source-ips 0.0.0.0/0 --source-ips ::/0 \
  --description "HTTPS"
```

---

## Step 3: Create the Server

```bash
hcloud server create \
  --name neodemos \
  --type ccx33 \
  --image ubuntu-24.04 \
  --ssh-key neodemos-deploy \
  --firewall neodemos-fw \
  --location fsn1

# Note the IP address from output — you'll need it for DNS and Kamal
hcloud server ip neodemos
```

---

## Step 4: Initial Server Hardening

SSH into the server and run the hardening script:

```bash
ssh -i ~/.ssh/neodemos_ed25519 root@$(hcloud server ip neodemos)
```

Once connected:

```bash
# --- Create deploy user ---
adduser deploy --disabled-password --gecos ""
mkdir -p /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys

# Grant sudo without password (needed for Kamal)
echo "deploy ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/deploy

# --- Disable root SSH login ---
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart sshd

# --- Firewall (belt + suspenders with hcloud firewall) ---
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# --- Automatic security updates ---
apt update && apt install -y unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades

# --- Set timezone ---
timedatectl set-timezone Europe/Amsterdam

# --- Swap (safety net for Qdrant memory spikes) ---
fallocate -l 4G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# --- Install Docker ---
curl -fsSL https://get.docker.com | sh
usermod -aG docker deploy
```

Disconnect and verify deploy user access:

```bash
ssh -i ~/.ssh/neodemos_ed25519 deploy@$(hcloud server ip neodemos) "docker --version"
```

---

## Step 5: Verify

```bash
# From your Mac
hcloud server describe neodemos

# Expected output includes:
#   Status: running
#   Server Type: ccx33
#   Image: ubuntu-24.04
#   Location: fsn1
```

---

## SSH Config (convenience)

Add to `~/.ssh/config`:

```
Host neodemos
    HostName 178.104.137.168
    User deploy
    IdentityFile ~/.ssh/neodemos_ed25519
    ForwardAgent no
```

Then simply: `ssh neodemos`

---

## Quick Reference

| Command | What it does |
|---------|-------------|
| `hcloud server list` | List all servers |
| `hcloud server ip neodemos` | Get server IP |
| `hcloud server ssh neodemos` | SSH into server (as root) |
| `hcloud server rebuild neodemos --image ubuntu-24.04` | Nuke and rebuild |
| `hcloud server resize neodemos --type ccx43` | Upgrade to 64 GB RAM |
| `hcloud server create-image --type snapshot neodemos` | Create snapshot before risky ops |
| `hcloud server delete neodemos` | Delete server (irreversible) |

---

## Cost Control

- Hetzner charges hourly when the server exists (running or stopped)
- **Delete** the server to stop billing; **stop** does NOT stop billing
- Snapshots cost EUR 0.0119/GB/mo
- Take a snapshot before upgrading, so you can roll back without re-provisioning

---

## Next Step

[02_DOMAIN_DNS.md](02_DOMAIN_DNS.md) — Register neodemos.nl and configure DNS
