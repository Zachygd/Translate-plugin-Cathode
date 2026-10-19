# Running Ollama from a second drive

There are two ways to keep Ollama off your system drive.

## Option A: only the models on another drive (easiest)

The Ollama program is small; the models are the big part. Let the installer handle it:

```bash
./install.sh --models-dir /mnt/data/ollama-models
```

This creates the folder, gives the Ollama service user ownership of it, and adds a systemd drop-in that sets `OLLAMA_MODELS`. Every model you pull from then on goes there.

To move models you already downloaded, stop the service, copy `/usr/share/ollama/.ollama/models` into the new folder, fix its ownership, and start the service again:

```bash
sudo systemctl stop ollama
sudo cp -a /usr/share/ollama/.ollama/models/. /mnt/data/ollama-models/
sudo chown -R ollama: /mnt/data/ollama-models
sudo systemctl start ollama
ollama list    # your models should still be listed
```

## Option B: the whole Ollama install on another drive

This puts both the Ollama program (including its multi-GB GPU libraries) and the models in one folder, e.g. `/mnt/data/Ollama`. Replace `YOURUSER` with your username.

> The drive must use a Linux filesystem (**ext4** or **btrfs**), not NTFS or exFAT, and must not be mounted with `noexec`.

**1. Download and extract Ollama into the folder**

```bash
sudo mkdir -p /mnt/data/Ollama/models
curl -fsSL https://ollama.com/download/ollama-linux-amd64.tar.zst \
  | sudo tar x --zstd -C /mnt/data/Ollama
sudo chown -R YOURUSER:YOURUSER /mnt/data/Ollama
sudo ln -sf /mnt/data/Ollama/bin/ollama /usr/local/bin/ollama
```

AMD users also need the ROCm package: `ollama-linux-amd64-rocm.tar.zst`, extracted the same way.

**2. Let SELinux run programs from that folder** (Fedora)

```bash
sudo semanage fcontext -a -t bin_t "/mnt/data/Ollama/bin(/.*)?"
sudo restorecon -Rv /mnt/data/Ollama/bin
```

**3. Create the service**

```bash
sudo nano /etc/systemd/system/ollama.service
```

```ini
[Unit]
Description=Ollama
After=network-online.target
RequiresMountsFor=/mnt/data/Ollama

[Service]
ExecStart=/mnt/data/Ollama/bin/ollama serve
User=YOURUSER
Group=YOURUSER
Environment="OLLAMA_MODELS=/mnt/data/Ollama/models"
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

`RequiresMountsFor` makes the service wait until the drive is mounted at boot.

**4. Start it**

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ollama
systemctl status ollama    # should say active (running)
```

If it fails with `203/EXEC`, recheck the filesystem type, the `noexec` mount option, and step 2.

**5. Install Cathode Translate**

The service is already set up, so Ollama doesn't need installing, but the installer still pulls the model and adds the speed settings and permissions:

```bash
./install.sh
```

## Updating Ollama (Option B)

Rerun the download command from step 1, then restart the service:

```bash
sudo systemctl stop ollama
curl -fsSL https://ollama.com/download/ollama-linux-amd64.tar.zst \
  | sudo tar x --zstd -C /mnt/data/Ollama
sudo chown -R YOURUSER:YOURUSER /mnt/data/Ollama
sudo restorecon -Rv /mnt/data/Ollama/bin
sudo systemctl start ollama
```
