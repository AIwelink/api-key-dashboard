# 服务器拉取最新版本并构建

在服务器执行下面的组合命令：

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

这条命令会依次拉取 `main` 最新代码、同步后端依赖、安装前端依赖、重新构建前端，并重启 `api-key-admin` 服务。
