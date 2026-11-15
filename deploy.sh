#!/usr/bin/env bash
# 本番サーバーで実行する更新の手順
set -euo pipefail

# SSH から呼ばれたときに uv が見つかるようにする
export PATH="$HOME/.local/bin:$PATH"

echo "==> アプリのフォルダーへ移動"
cd /var/www/marche-app

echo "==> 最新のコードを取得（main、fast-forward のみ）"
git pull --ff-only origin main

echo "==> 依存パッケージを同期（本番用のみ）"
uv sync --frozen --no-dev

echo "==> データベースのマイグレーションを適用"
uv run --frozen --no-dev flask db upgrade

echo "==> アプリを再起動"
sudo -n systemctl restart marche-app

echo "==> 起動を確認（2秒待つ）"
sleep 2
systemctl is-active marche-app

echo "==> 更新が完了しました"
