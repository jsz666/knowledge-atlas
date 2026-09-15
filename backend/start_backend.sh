#!/usr/bin/env bash
# 在 WSL 内启动知识星图后端（数据库走 app/config.py 默认值，与 start.sh 保持一致）
set -e
cd /mnt/c/Users/23793/Desktop/Project/knowledge-atlas/backend
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
