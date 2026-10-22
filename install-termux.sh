#!/usr/bin/env bash
# ===================================================================
#  DBAgent Termux 一键安装启动
#
#  用法：
#    bash install-termux.sh                  # 自动找 zip 并安装到 ~/dbagent
#    bash install-termux.sh ./dbagent.zip    # 指定 zip
#    DBAGENT_KEY=sk-xxx bash install-termux.sh   # 非交互配置 Key
#
#  做的事：
#    1. 检查 Termux 环境，安装 python / unzip
#    2. 解压项目到 ~/dbagent（已存在则覆盖更新，保留 workspace/data/work 产出）
#    3. 安装 requirements.txt 依赖（可选依赖尽力而为）
#    4. 配置 API Key（已有则跳过）
#    5. 启动 dbagent 对话模式
# ===================================================================
set -uo pipefail

TARGET="${DBAGENT_HOME:-$HOME/dbagent}"
REQ_TIMEOUT=1800

say()  { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[警告] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[错误] %s\033[0m\n' "$*" >&2; exit 1; }

# ---------- 0. 环境检查 ----------
if [ -z "${PREFIX:-}" ] || [ ! -d "${PREFIX:-/nonexistent}" ]; then
    warn "看起来不是 Termux 环境。脚本按 Termux 路径处理，其他 Linux 可手动 pip install -r requirements.txt"
fi

# ---------- 1. 安装基础包 ----------
# 基础包只是锦上添花：python3/unzip 已就绪时，pkg 失败（如 dpkg 被打断）
# 不能阻断后面的代码解压，否则用户以为装上了其实跑的还是旧代码。
if command -v pkg >/dev/null 2>&1; then
    say "安装基础包（python / unzip）"
    pkg update -y >/dev/null 2>&1 || warn "pkg update 失败，继续"
    pkg install -y python unzip \
        || warn "pkg install 失败；若 python3/unzip 已可用可忽略"
fi
if ! command -v python3 >/dev/null 2>&1; then
    die "找不到 python3。若报 dpkg 错误，先执行：dpkg --configure -a"
fi
if ! command -v unzip >/dev/null 2>&1; then
    die "找不到 unzip。若报 dpkg 错误，先执行：dpkg --configure -a"
fi

# ---------- 2. 找到并解压 zip ----------
ZIP="${1:-}"
if [ -z "$ZIP" ]; then
    for cand in \
        "$HOME/storage/downloads"/dbagent-*.zip \
        "$HOME/storage/shared/Download"/dbagent-*.zip \
        "$HOME/Download"/dbagent-*.zip \
        "$HOME"/dbagent-*.zip \
        ./dbagent-*.zip; do
        if [ -f "$cand" ]; then ZIP="$cand"; break; fi
    done
fi
[ -n "$ZIP" ] && [ -f "$ZIP" ] || die "找不到 zip；请把 dbagent-*.zip 放到 ~/Download 或作为第一个参数传入"
say "解压：$ZIP → $TARGET"
mkdir -p "$TARGET"
# 直接覆盖更新：目录已存在时未包含在 zip 里的文件（如 work 产出、本地配置）不会被删
rm -rf "$TARGET.tmp"
unzip -oq "$ZIP" -d "$TARGET.tmp" || die "解压失败（文件损坏？）"
if [ -d "$TARGET.tmp/dbagent" ]; then
    SRC="$TARGET.tmp/dbagent"
else
    SRC="$TARGET.tmp"
fi
mkdir -p "$HOME/.dbagent"
cp -a "$SRC/." "$TARGET/"
rm -rf "$TARGET.tmp"
cd "$TARGET" || die "进入 $TARGET 失败"

# ---------- 3. 安装 Python 依赖 ----------
# 清华源上部分包（chardet、python-docx）索引缺失，取不到版本。
# 这里不依赖 ~/.config/pip/pip.conf，显式按顺序试源，失败自动换下一个。
PIP_INDEXES="https://pypi.tuna.tsinghua.edu.cn/simple https://pypi.org/simple"
# 超时/重试：默认配置会无限等索引，看起来像卡死
PIP_OPTS="--timeout 30 --retries 2"

pip_try() {  # pip_try <包>...
    for idx in $PIP_INDEXES; do
        timeout 900 python3 -m pip install $PIP_OPTS --index-url "$idx" "$@" && return 0
    done
    return 1
}

# requirements.txt 里的包名 → 可 import 的模块名。
# 已能 import 的包直接跳过：Termux 上 pip 重新解析整棵依赖树可能卡很久。
pkg_module() {
    case "$1" in
        Pillow*)       echo PIL ;;
        python-docx*)  echo docx ;;
        python-pptx*)  echo pptx ;;
        requests*)     echo requests ;;
        chardet*)      echo chardet ;;
        openpyxl*)     echo openpyxl ;;
        pypdf*)        echo pypdf ;;
        reportlab*)    echo reportlab ;;
        validators*)   echo validators ;;
        defusedxml*)   echo defusedxml ;;
        *)             echo "" ;;
    esac
}

# Pillow / lxml / cryptography 带 C 扩展，pip 会下源码现场编译，
# Termux 上可能卡几十分钟。仓库里有预编译版，优先用 pkg。
# 不重定向输出：Termux 上下载几十 MB 可能好几分钟，看不到进度会以为卡死。
# DBAGENT_SKIP_PKGS=1 可整段跳过（核心对话只依赖标准库）。
if [ "${DBAGENT_SKIP_PKGS:-0}" != "1" ] && command -v pkg >/dev/null 2>&1; then
    say "安装 Termux 预编译包（可能要几分钟，下方是下载进度）"
    timeout 1200 pkg install -y python-pillow \
        || warn "python-pillow 未装，Pillow 稍后回退到 pip"
    timeout 600 pkg install -y python-lxml || warn "python-lxml 未装（docx 可能受影响）"
    timeout 600 pkg install -y python-cryptography || true
    timeout 600 pkg install -y libjpeg-turbo zlib poppler || true
fi

say "安装 Python 依赖（逐个安装，已满足的自动跳过）"
python3 -m pip install $PIP_OPTS --upgrade pip wheel \
    || warn "pip 升级失败，继续"
# 逐个安装：单个包失败不阻断其余依赖，最后统一报告缺哪些
if [ -f requirements.txt ]; then
    TOTAL=$(grep -cvE '^[[:space:]]*($|#)' requirements.txt || true)
    N=0
    FAILED_PKGS=""
    while read -r pkg; do
        case "$pkg" in
            ""|\#*) continue ;;
        esac
        N=$((N + 1))
        MOD=$(pkg_module "$pkg")
        if [ -n "$MOD" ] && python3 -c "import $MOD" 2>/dev/null; then
            printf '[%d/%s] %s 已安装，跳过\n' "$N" "${TOTAL:-?}" "$pkg"
            continue
        fi
        printf '[%d/%s] %s\n' "$N" "${TOTAL:-?}" "$pkg"
        pip_try "$pkg" || FAILED_PKGS="$FAILED_PKGS $pkg"
    done < requirements.txt
    if [ -n "$FAILED_PKGS" ]; then
        warn "以下包装不上：$FAILED_PKGS"
        warn "可手动逐个试：pip install --index-url https://pypi.org/simple <包名>"
    fi
    python3 -c "import chardet" 2>/dev/null && echo "chardet 已就绪" \
        || pip_try chardet || warn "chardet 仍装不上；非 UTF-8 网页的编码检测会退化为 utf-8"
fi
pip_try lxml || warn "lxml 未装（可选）"
pip_try pdfplumber || warn "pdfplumber 未装（可选）"
pip_try cryptography || warn "cryptography 未装（可选）"

# ---------- 4. 配置 API Key ----------
CFG="$HOME/.dbagent/agent_coder.json"
if [ -s "$CFG" ] || [ -n "${AGENT_API_KEY:-}" ]; then
    say "已检测到 API Key，跳过配置"
else
    KEY="${DBAGENT_KEY:-}"
    if [ -z "$KEY" ]; then
        say "配置 API Key（直接回车可跳过，之后可用 ./run.sh --setup 再配）"
        printf 'API Key（输入不回显）: '
        read -rs KEY || true
        echo
    fi
    if [ -n "$KEY" ]; then
        python3 - "$KEY" <<'PY'
import json, os, sys
key = sys.argv[1].strip()
path = os.path.expanduser("~/.dbagent/agent_coder.json")
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump({"api_key": key,
               "base_url": "https://apihub.agnes-ai.com/v1",
               "model": "agnes-2.5-flash"}, f, ensure_ascii=False, indent=2)
os.chmod(path, 0o600)
print("已写入", path)
PY
    else
        warn "未配置 Key，启动后可用 /setup 或 ./run.sh --setup"
    fi
fi

# ---------- 5. 启动 ----------
say "启动 DBAgent（Termux 目录隔离沙箱，无需 root）"
exec bash run.sh
