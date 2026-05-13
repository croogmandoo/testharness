# Deploying as a VMware/VirtualBox Appliance (OVA / OVF)

The cleanest path to an OVA package is to **build it once from an Ubuntu
Server template, then export to OVF/OVA via your hypervisor**. The harness
ships a turnkey installer (`scripts/install-on-ubuntu.sh`) that handles all
the bring-up inside the VM, so the appliance build is essentially:

1. Spin up an Ubuntu Server 24.04 VM
2. Run the installer
3. Export to OVA

That's it. No Packer, no nested virtualisation, no CI gymnastics — just
your existing hypervisor's export tooling.

---

## Step 1 — Build the template VM

| Hypervisor | Steps |
|---|---|
| **vSphere / vCenter / ESXi** | New VM → Linux → Ubuntu Linux (64-bit). 2 vCPU, 4 GB RAM, 40 GB thin-provisioned disk. Attach Ubuntu Server 24.04 ISO. Install with default options; create a sudo user. |
| **VMware Workstation / Fusion** | File → New Virtual Machine → ISO → Ubuntu Server 24.04. Same sizing as above. |
| **VirtualBox** | New → Linux → Ubuntu (64-bit). Mount the ISO, install. |
| **Hyper-V** | New VM (Gen 2). Disable secure boot for Linux. 2 vCPU, 4 GB RAM. |
| **Proxmox** | Create VM, attach Ubuntu Server 24.04 ISO, install. |

**Recommended sizing:**

- **vCPU:** 2 (minimum). Add more for >50 apps.
- **RAM:** 4 GB. Playwright/Chromium is the heaviest component.
- **Disk:** 40 GB thin-provisioned. Screenshots + run history at retention
  90 days for ~20 apps fits in ~5 GB; sizing has headroom.
- **NIC:** bridged or routed — the harness exposes tcp/9552 to the network.

Install Ubuntu Server with default options. Enable OpenSSH server during
install so you can `ssh` in. Don't install any "featured server snaps" —
they're not needed.

---

## Step 2 — Run the turnkey installer

SSH in as your sudo user and run:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/croogmandoo/testharness/master/scripts/install-on-ubuntu.sh \
  | sudo bash
```

What the installer does:

- Installs Docker Engine + the compose plugin (if not already present)
- Creates `/opt/harness/{data,apps}` with the right uid (10001)
- Writes a minimal `/opt/harness/config.yaml` and `docker-compose.yml`
- Creates a systemd unit `harness.service` so the container restarts on boot
- Pulls `ghcr.io/croogmandoo/testharness:latest` and starts it

After ~2 minutes, the installer prints a summary with the IP and URL.
Verify the harness is up:

```bash
curl http://<vm-ip>:9552/health
# {"status":"ok"}
```

Don't go through the `/setup` admin-creation flow yet — leave the VM
unconfigured so operators set up their own admin on first boot of the
deployed appliance.

---

## Step 3 — Generalise the VM (recommended before export)

A few hygiene steps so the appliance is fresh for whoever imports it:

```bash
# Remove SSH host keys (regenerated on first boot)
sudo rm -f /etc/ssh/ssh_host_*

# Clean apt cache and history
sudo apt-get clean
sudo cloud-init clean --logs    # if cloud-init is present
truncate -s 0 ~/.bash_history && history -c

# Optional: zero out free space so the exported disk compresses well
sudo dd if=/dev/zero of=/EMPTY bs=1M status=progress || true
sudo rm -f /EMPTY

# Power off cleanly
sudo poweroff
```

When the VM boots after import, systemd's `ssh-keygen.service` regenerates
host keys automatically; `cloud-init` (if present) gives the new VM a
unique machine-id.

---

## Step 4 — Export to OVA/OVF

| Hypervisor | Command / UI |
|---|---|
| **vSphere / vCenter** | Right-click VM → Template → Export OVF Template. Choose **single .ova** file. |
| **ESXi (free)** | Same UI as vSphere when the host is registered. Or use `ovftool`: `ovftool vi://root@esxi/MyVM ./harness-appliance.ova` |
| **VMware Workstation** | File → Export to OVF. Pick destination. |
| **VirtualBox** | File → Export Appliance → select VM → choose OVF 2.0 or 1.0 format → write `harness-appliance.ova`. |
| **Hyper-V** | Hyper-V doesn't natively export OVA. Use Microsoft Virtual Machine Converter (MVMC) or export-then-convert with `ovftool`. |
| **Proxmox** | Use `qm export` to a `.vma` then convert with `qemu-img` + write a hand-rolled OVF descriptor. Easier: install [`virt-v2v`](https://libguestfs.org/virt-v2v.1.html). |

A standalone `.ova` is a single tarball containing the OVF descriptor, the
disk image (`.vmdk`), and a manifest. ~1.5 GB for a fresh harness install
(most of which is the Playwright/Chromium layers in the Docker image).

`ovftool` lives at `/usr/bin/ovftool` if you install VMware OVF Tool;
download from <https://developer.broadcom.com/tools/open-virtualization-format-ovf-tool>.

---

## Step 5 — Distribute & deploy

The receiving operator:

1. **Import the OVA** into their hypervisor (vSphere "Deploy OVF Template", VirtualBox "Import Appliance", etc.).
2. **Boot the VM, assign IP** (DHCP works; static via netplan if needed).
3. **Browse to `http://<vm-ip>:9552`** → redirected to `/setup` → create admin account.
4. **Configure apps** — drop YAML files into `/opt/harness/apps/` or use the form-based editor at `/apps/new`.
5. **Configure alerts** — edit `/opt/harness/config.yaml`, add Teams/Slack/Discord/email webhook URLs, then `sudo systemctl restart harness`.
6. **Optional: front with HTTPS** — see "Reverse proxy" in `docs/guides/deployment.md`.

---

## Updating the appliance after deploy

Inside the running VM:

```bash
cd /opt/harness
sudo docker compose pull
sudo systemctl restart harness
```

The harness's `data/` directory (SQLite DB + screenshots + secret.key) is
a bind-mount, so it persists across image upgrades. Schema migrations
run automatically on startup.

To rebuild the appliance from a newer base image:

```bash
sudo docker compose pull
sudo systemctl restart harness
# verify
curl http://localhost:9552/health
# now re-export to OVA via the same hypervisor flow
```

---

## What lives where inside the appliance

```
/opt/harness/
├── docker-compose.yml          # service definition
├── config.yaml                 # harness config (edit this)
├── .env                        # secrets (mode 0600; never commit/export)
├── apps/                       # app YAML files
└── data/
    ├── harness.db              # SQLite DB
    ├── secret.key              # Fernet/session signing key — back this up
    ├── ca-bundle.pem           # generated; do not edit
    └── screenshots/            # per-run failure + step screenshots

/etc/systemd/system/harness.service   # boot/restart unit
```

> ⚠️ **Critical:** `data/secret.key` encrypts every secret in the DB AND
> signs session cookies. Losing it makes stored secrets unrecoverable
> and invalidates every session. Back it up the same way you back up
> the database. Documented in `docs/reference/auth.md`.

---

## Air-gapped / disconnected environments

The default appliance pulls the harness image from ghcr.io on first
boot. For air-gapped sites:

1. On a connected host:
   ```bash
   docker pull ghcr.io/croogmandoo/testharness:latest
   docker save ghcr.io/croogmandoo/testharness:latest \
       | gzip > harness-image.tar.gz
   ```
2. Copy `harness-image.tar.gz` onto the appliance build VM at
   `/opt/harness/harness-image.tar.gz`.
3. In `/opt/harness/docker-compose.yml`, replace the `image:` line with
   `image: testharness:loaded` and add a build step (or use `docker load`
   in the systemd unit's `ExecStartPre`).
4. Re-export the OVA — receiving sites now have everything in one file.

A future improvement would be a single `--air-gapped` flag on the
installer that automates this; track in the roadmap.

---

## Comparison: this approach vs Packer / vagrant

| | This guide | Packer (vmware-iso/virtualbox-iso) |
|---|---|---|
| Build env required | Just your existing hypervisor | Linux build host + Packer + hypervisor SDK |
| Nested virt | Not needed | Required for cloud builds |
| Build time | ~10 min interactive + 5 min export | ~20 min headless |
| Reproducibility | Manual; rebuild from template | Fully scripted, git-controlled |
| Drift | Easy to accidentally diverge | None — every build is identical |
| CI integration | Self-hosted runner with hypervisor SDK | Same |

For most teams, the manual flow is fine — appliances ship once or twice
a year, the bootstrap script is the reproducible bit. Move to Packer
when you need monthly automated builds.
