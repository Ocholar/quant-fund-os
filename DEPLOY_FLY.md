# Deploying to Fly.io (Paper & Live Ready)

We have reconfigured the Quant Fund OS for a robust Fly.io deployment with persistent storage.

### 1. Prerequisites

- Fly.io Account with a payment method (as you've already done).
- `flyctl` installed on your local machine.

### 2. Create the App & Volume

Run these in your terminal within the project folder:

```bash
# Register the app name
fly apps create quant-fund-os

# Create a 1GB Persistent Volume for your database
fly volumes create quant_data --size 1 --region jnb
```

### 3. Deploy

Launch the app using the pre-configured `fly.toml`:

```bash
fly deploy
```

### 4. Configuration

The `fly.toml` is already set up to:

- Use port **8080** (standard for cloud).
- Mount the database at **/app/data/quant.db** (so your trades survive restarts).
- Run in **Paper Mode** by default (`LIVE_TRADING=false`).

### 5. Transition to Live

When you are ready to use your real MEXC balance:

1. Increase your `INITIAL_EQUITY` to $100+ (to hit MEXC's 1.0 USDT minimum).
2. Update the secret environment variables:

    ```bash
    fly secrets set LIVE_TRADING=true MEXC_API_KEY=xxx MEXC_API_SECRET=xxx
    ```

3. Deploy again: `fly deploy`.

Access your dashboard at: `https://quant-fund-os.fly.dev/dashboard`.
