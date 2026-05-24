# Oracle Cloud Deployment Guide

> **Quant Fund OS** — Deploy to Oracle Cloud Always Free for 24/7 autonomous paper trading.

---

## 1. Create the Oracle VM

1. Go to [cloud.oracle.com](https://cloud.oracle.com) → **Compute → Instances → Create Instance**
2. Configure:
   - **Name:** `quant-fund-os`
   - **Shape:** `VM.Standard.A1.Flex` *(Always Free — Ampere ARM)*
   - **OCPUs:** 4 | **RAM:** 24 GB
   - **OS:** Ubuntu 22.04
   - **Boot Volume:** 50 GB
3. Under **Add SSH Keys**, upload your public key or let Oracle generate a key pair. Download the private key.
4. Click **Create**.
5. Note the **Public IP address** once the instance is running.

---

## 2. Open Port 8000 (Firewall)

1. In Oracle Cloud → **Networking → Virtual Cloud Networks → your VCN → Security Lists → Default Security List**
2. Click **Add Ingress Rules**:
   - **Source CIDR:** `0.0.0.0/0`
   - **Protocol:** TCP
   - **Destination Port:** `8000`
3. Save.

Also open it at the OS level once you SSH in:

```bash
sudo iptables -I INPUT -p tcp --dport 8000 -j ACCEPT
sudo netfilter-persistent save
```

---

## 3. Connect via SSH

```bash
ssh -i /path/to/your_private_key.pem ubuntu@<ORACLE_PUBLIC_IP>
```

---

## 4. Install Docker

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose
sudo usermod -aG docker ubuntu
newgrp docker
```

Verify:

```bash
docker --version
docker-compose --version
```

---

## 5. Upload Your `.env` File

From your **local machine** (PowerShell):

```powershell
scp -i C:\path\to\key.pem C:\Users\Administrator\Documents\quant-fund-os\.env ubuntu@<ORACLE_PUBLIC_IP>:~/quant-fund-os/.env
```

> **Never commit `.env` to Git** — it contains your API keys.

---

## 6. Clone and Deploy

On the Oracle VM:

```bash
git clone https://github.com/Ocholar/quant-fund-os.git
cd quant-fund-os
# .env should already be here from the scp step above
docker-compose up -d --build
```

This builds the image and starts:

- `main.py` — the trading bot (background)
- Uvicorn API + Dashboard on port 8000 (foreground)

---

## 7. Verify It's Running

```bash
# Check container status
docker-compose ps

# Tail live logs
docker-compose logs -f
```

Open the dashboard in your browser:

```
http://<ORACLE_PUBLIC_IP>:8000/dashboard
```

---

## 8. Useful Ongoing Commands

| Task | Command |
|---|---|
| View live logs | `docker-compose logs -f` |
| Restart bot | `docker-compose restart` |
| Stop everything | `docker-compose down` |
| Pull latest code | `git pull && docker-compose up -d --build` |
| Check resource usage | `docker stats` |

---

## 9. Keep DB Safe (Backups)

The `quant.db` file is stored in the Docker named volume `quant_data`. To back it up:

```bash
docker cp quant-fund-os-quant-1:/app/quant.db ~/quant_backup_$(date +%Y%m%d).db
```

---

## Notes

- **Live trading is OFF** by default (`LIVE_TRADING=false` in `.env`). Only flip to `true` after a successful 48h paper burn-in.
- The database persists across container restarts via the Docker named volume.
- Timezone for all timestamps is **Kenyan Time (GMT+3)** — baked into the SQL queries.
