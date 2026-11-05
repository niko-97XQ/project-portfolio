#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/home/ubuntu/forthatday-agent-v2}"
PUBLIC_URL="${PUBLIC_URL:-https://api.forthatday-studio.com}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/backups}"

cd "$APP_DIR"

fail() {
  echo "部署失败：$*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "未找到 Docker"
docker compose version >/dev/null 2>&1 || fail "未找到 Docker Compose 插件"
test -f .env.production || fail "缺少 .env.production"
test -f Dockerfile || fail "缺少 Dockerfile"
test -f docker-compose.yml || fail "缺少 docker-compose.yml"
test -f private/payment-policy.json || fail "缺少 private/payment-policy.json"
test -f private/payment-codes/wechat.jpg || fail "缺少微信付款码"
test -f private/payment-codes/alipay.jpg || fail "缺少支付宝付款码"

mkdir -p data private/catalog private/wecom-media private/payment-codes "$BACKUP_DIR"
chmod 600 .env.production

stamp="$(date +%Y%m%d_%H%M%S)"
if test -f data/agent_v2.db; then
  cp -p data/agent_v2.db "$BACKUP_DIR/agent_v2.db.$stamp"
fi
cp -p .env.production "$BACKUP_DIR/.env.production.$stamp"
cp -p Caddyfile "$BACKUP_DIR/Caddyfile.$stamp"

echo "[1/6] 检查 Docker Compose 配置"
sudo docker compose config >/dev/null

echo "[2/6] 构建 Agent 镜像"
sudo docker compose build agent

echo "[3/6] 启动服务"
sudo docker compose up -d --force-recreate

echo "[4/6] 等待 Agent 健康"
for attempt in $(seq 1 30); do
  if sudo docker compose exec -T agent python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)" >/dev/null 2>&1; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    sudo docker compose ps
    sudo docker compose logs --tail=200 agent
    fail "Agent 健康检查超时"
  fi
  sleep 2
done

echo "[5/6] 检查公网健康地址"
curl --fail --silent --show-error "$PUBLIC_URL/healthz"
echo

echo "[6/6] 当前服务状态"
sudo docker compose ps
echo "部署完成。备份目录：$BACKUP_DIR"
