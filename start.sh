#!/usr/bin/env bash
# 《知识星图》一键启动脚本(WSL)
# 用法: ./start.sh                       # 后端 8000 / 前端 5173
#      VITE_PORT=5174 ./start.sh         # 指定前端端口
#      UVICORN_PORT=9000 ./start.sh
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UVICORN_PORT="${UVICORN_PORT:-8000}"
VITE_PORT="${VITE_PORT:-5173}"
export PATH="$HOME/.local/bin:$PATH"

log(){ echo "[start] $*"; }

command -v python3 >/dev/null 2>&1 || { echo "缺少 python3,请先执行: sudo apt install -y python3"; exit 1; }

# ---------- pip ----------
if ! python3 -m pip --version >/dev/null 2>&1; then
  log "未检测到 pip,尝试安装到用户目录"
  ( curl -sSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py || wget -q -O /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py ) \
    && python3 /tmp/get-pip.py --user --break-system-packages -q
  export PATH="$HOME/.local/bin:$PATH"
fi

# ---------- Node(Linux 优先,避免 Windows npm 的 UNC 路径陷阱) ----------
NODE_BIN=""
if [ -s "$HOME/.nvm/nvm.sh" ]; then
  # shellcheck disable=SC1090
  . "$HOME/.nvm/nvm.sh"
  nvm use 20 >/dev/null 2>&1 || nvm use --lts >/dev/null 2>&1
fi
if command -v node >/dev/null 2>&1; then
  case "$(command -v node)" in
    /mnt/*) ;;  # 跳过 Windows 版 node(无法 exec linux 二进制)
    *) NODE_BIN="$(command -v node)" ;;
  esac
fi
if [ -z "$NODE_BIN" ]; then
  for d in "$HOME"/node-v*/bin; do
    if [ -x "$d/node" ]; then NODE_BIN="$d/node"; break; fi
  done
fi
if [ -z "$NODE_BIN" ]; then
  for c in /mnt/e/Nodejs/node.exe "/mnt/c/Program Files/nodejs/node.exe" /mnt/d/Nodejs/node.exe; do
    if [ -x "$c" ]; then
      mkdir -p "$HOME/.local/bin"
      printf '#!/usr/bin/env bash\nexec "%s" "$@"\n' "$c" > "$HOME/.local/bin/node"
      chmod +x "$HOME/.local/bin/node"
      export PATH="$HOME/.local/bin:$PATH"
      NODE_BIN="$(command -v node)"
      log "复用 Windows 侧 Node(不推荐,esbuild 可能不兼容):$c"
      break
    fi
  done
fi
[ -n "$NODE_BIN" ] || {
  echo "未找到可用的 Linux Node,请执行: curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash && nvm install 20"
  exit 1
}
log "Node: $NODE_BIN ($(node --version 2>/dev/null))"

# ---------- 清理可能残留的旧实例 ----------
pkill -f "uvicorn app.main:app" 2>/dev/null || true
pkill -f "vite/bin/vite.js" 2>/dev/null || true
sleep 1

# ---------- 后端(常驻,脱离 wsl 会话) ----------
log "启动后端 on :$UVICORN_PORT  -> /tmp/atlas_be.log"
setsid bash -c '
  cd "'"$ROOT"'/backend"
  if python3 -m venv .venv >/dev/null 2>&1 && [ -x .venv/bin/python3 ]; then
    source .venv/bin/activate
    python3 -m pip install -q -r requirements.txt || python3 -m pip install --user --break-system-packages -q -r requirements.txt
  else
    rm -rf .venv
    python3 -m pip install --user --break-system-packages -q -r requirements.txt
  fi
  exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port '"$UVICORN_PORT"'
' >/tmp/atlas_be.log 2>&1 < /dev/null &

# ---------- 前端(直接用 linux node 跑 vite,绕过 npm 的 Windows cmd 包装) ----------
log "启动前端 on :$VITE_PORT  -> /tmp/atlas_fe.log"
setsid bash -c '
  cd "'"$ROOT"'/frontend"
  exec node node_modules/vite/bin/vite.js --host --port '"$VITE_PORT"'
' >/tmp/atlas_fe.log 2>&1 < /dev/null &

log "前端 http://localhost:$VITE_PORT   后端 http://localhost:$UVICORN_PORT/docs"
log "查看日志: tail -f /tmp/atlas_fe.log /tmp/atlas_be.log"
