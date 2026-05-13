#!/bin/bash

DIR="$(cd "$(dirname "$0")" && pwd)"
echo "👀 开始监控 $DIR，档案变更时自动同步到 GitHub..."

fswatch -o "$DIR" \
  --exclude ".git" \
  --exclude "__pycache__" \
  --exclude "*.pyc" \
  | while read; do
    cd "$DIR"
    # 检查是否有变更
    if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
      TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
      git add .
      git commit -m "auto sync: $TIMESTAMP"
      git push
      echo "✅ [$TIMESTAMP] 已同步到 GitHub"
    fi
  done
