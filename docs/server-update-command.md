# 服务器更新命令

服务器项目目录：

```bash
/www/wwwroot/serve/local/api-key-dashboard
```

## 首次切换到新 Git 仓库

服务器旧目录已经存在时，先把 `origin` 切到新仓库：

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

## 后续日常更新

远程仓库已经切好后，用下面这条即可：

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

## 后端启动命令

如果需要手动启动后端：

```bash
cd /www/wwwroot/serve/local/api-key-dashboard/backend && \
python3 -m uv run python3 -m app.run
```

systemd 服务建议使用：

```ini
WorkingDirectory=/www/wwwroot/serve/local/api-key-dashboard/backend
ExecStart=/usr/bin/python3 -m uv run python3 -m app.run
```

## 查看服务状态和日志

```bash
sudo systemctl status api-key-admin
journalctl -u api-key-admin -n 200 --no-pager
journalctl -u api-key-admin -f
```
