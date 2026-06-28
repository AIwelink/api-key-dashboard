# Server Update Commands

Server project directory:

```bash
/www/wwwroot/serve/local/api-key-dashboard
```

Current Git repository:

```text
SSH: git@github.com:AIwelink/api-key-dashboard.git
HTTPS: https://github.com/AIwelink/api-key-dashboard.git
```

## First Switch To The New Repository

Run this when the existing server directory still points to the old repository:

```bash
cd /www/wwwroot/serve/local/api-key-dashboard && \
git remote set-url origin git@github.com:AIwelink/api-key-dashboard.git && \
git fetch origin && \
git pull --ff-only origin main && \
cd backend && \
python3 -m uv sync && \
cd ../frontend && \
npm install && \
npm run build && \
sudo systemctl restart api-key-admin
```

## Daily Update

Run this after `origin` already points to the new repository:

```bash
cd /www/wwwroot/serve/local/api-key-dashboard && \
git pull --ff-only origin main && \
cd backend && \
python3 -m uv sync && \
cd ../frontend && \
npm install && \
npm run build && \
sudo systemctl restart api-key-admin
```

## Manual Backend Start

```bash
cd /www/wwwroot/serve/local/api-key-dashboard/backend && \
python3 -m uv run python3 -m app.run
```

Recommended systemd service lines:

```ini
WorkingDirectory=/www/wwwroot/serve/local/api-key-dashboard/backend
ExecStart=/usr/bin/python3 -m uv run python3 -m app.run
```

## Logs

```bash
sudo systemctl status api-key-admin
journalctl -u api-key-admin -n 200 --no-pager
journalctl -u api-key-admin -f
```
