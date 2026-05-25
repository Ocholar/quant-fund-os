# Deploying to Google Cloud Platform (Always Free)

Google Cloud offers a "Free Tier" that includes one **e2-micro** instance forever. This is perfect for the Quant Fund OS.

### 1. Prerequisites

- A Google Cloud Account (Credit Card required for verification, but you won't be charged if you stay in the limits).
- Docker installed on your local machine to build the image.

### 2. Prepare the Instance

Go to the [Google Cloud Console](https://console.cloud.google.com/):

1. **Create a Project**: Name it `quant-fund-os`.
2. **Compute Engine**: Enable the API.
3. **VM Instance**: Click "Create Instance".
    - **Region**: Choose `us-central1` (Iowa), `us-east1` (South Carolina), or `us-west1` (Oregon). these are the **ONLY** free regions.
    - **Machine configuration**: Series `E2`, Machine Type `e2-micro` (2 vCPU, 1 GB RAM).
    - **Boot Disk**:
        - Type: `Standard Persistent Disk` (Do NOT choose SSD or Balanced).
        - Size: 10 GB to 30 GB.
        - OS: `Ubuntu 22.04 LTS`.
    - **Firewall**: Check "Allow HTTP traffic" and "Allow HTTPS traffic".
    - **Advanced Options** -> **Networking**: Ensure you use "Standard" networking if available to stay in the free tier.

### 3. Install Docker on the VM

Once the VM is running, SSH into it and run:

```bash
sudo apt-get update
sudo apt-get install docker.io docker-compose -y
sudo usermod -aG docker $USER
# Log out and log back in
```

### 4. Deploy the Bot

1. Clone your repo to the VM.
2. Create your `.env` file from the template.
3. Run with Docker Compose:

```bash
docker-compose up -d --build
```

### 5. Access the Dashboard

The dashboard runs on port `8002`. You need to open this in your GCP Firewall:

1. Go to **VPC Network** -> **Firewall**.
2. Create Firewall Rule:
    - Name: `allow-quant-dashboard`
    - Target tags: `http-server`
    - Source filters: `0.0.0.0/0`
    - Protocols and ports: `tcp:8002`

You can now access it at `http://[YOUR_INSTANCE_IP]:8002/dashboard`.
