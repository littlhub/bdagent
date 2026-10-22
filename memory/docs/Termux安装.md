# DBAgent Termux 安装指南

> 适用：Android + Termux（无 root）。项目包：`dbagent-*.zip`。
> 本文按 zip 位于 `/storage/emulated/0/DeepSeekwork/work/` 编写，路径不同改 `Z` 即可。

---

## 快速路径（只想先跑起来）

DBAgent 核心对话只依赖 Python 标准库，**不装任何第三方包也能启动**。

```bash
termux-setup-storage                      # 弹窗点"允许"，已授权可跳过
pkg install -y python unzip

Z=/storage/emulated/0/DeepSeekwork/work/dbagent-20261020.zip
rm -rf ~/.dbagent-boot
mkdir -p ~/.dbagent-boot ~/dbagent
unzip -oq "$Z" -d ~/.dbagent-boot
cp -a ~/.dbagent-boot/dbagent/. ~/dbagent/   # 首次安装

cd ~/dbagent && bash run.sh
```

第三方包按需再装：用到哪个技能报 `ModuleNotFoundError`，再补对应包（见第 5 节）。

---

## 完整安装

### 0. 存储权限

```bash
termux-setup-storage
```

### 1. 换 Termux 软件源

清华的 Termux 源已下线（`termux-change-repo` 里显示 bad），用中科大 USTC：

```bash
echo "deb https://mirrors.ustc.edu.cn/termux/apt/termux-main stable main" > $PREFIX/etc/apt/sources.list
pkg update -y
```

备用源：`https://mirrors.bfsu.edu.cn/termux/apt/termux-main`

### 2. 基础工具

```bash
pkg install -y python unzip
```

### 3. 清掉坏掉的 PyPI 源配置（重要）

**不要给 pip 配清华 PyPI 镜像**。清华源上 `chardet`、`python-docx` 等包的索引缺失，pip 会直接报
`Could not find a version that satisfies the requirement ... (from versions: none)`。

如果之前配过，先清掉：

```bash
python -m pip config unset global.index-url 2>/dev/null
python -m pip config unset global.trusted-host 2>/dev/null
python -m pip install -U pip wheel
```

`install-termux.sh` 内部不依赖 pip.conf，会显式按「清华源 → 官方源」顺序试，失败自动换源。

### 4. 带 C 扩展的包用 Termux 预编译版

Pillow（47MB 源码）、lxml、cryptography 如果交给 pip，会现场编译，Termux 上可能卡几十分钟。

```bash
pkg install -y python-pillow
pkg install -y python-lxml python-cryptography 2>/dev/null || true
pkg install -y nodejs poppler 2>/dev/null || true     # 可选：pptx / pdf 技能
```

### 5. 纯 Python 依赖

核心：

```bash
pip install --no-deps --timeout 30 -i https://pypi.org/simple \
  requests chardet openpyxl
```

按需补充：

| 包 | 用途 | 说明 |
|---|---|---|
| `python-docx` | Word 处理 | 依赖 lxml，先做第 4 节 |
| `python-pptx` | PPT 处理 | 体积大，用到再装 |
| `pypdf` | PDF 读取 | 纯 Python |
| `reportlab` | PDF 生成 | 编译较慢，用到再装 |
| `validators` / `defusedxml` | 校验 / 安全解析 | 纯 Python |

`--no-deps` 跳过整棵依赖树解析，避免 pip 在解析阶段长时间无响应。

验证：

```bash
python -c "import requests, openpyxl; print('核心依赖 OK')"
```

### 6. 解出安装脚本并执行

```bash
Z=/storage/emulated/0/DeepSeekwork/work/dbagent-20261020.zip
rm -rf ~/.dbagent-boot
mkdir -p ~/.dbagent-boot
unzip -oq "$Z" -d ~/.dbagent-boot
bash ~/.dbagent-boot/dbagent/install-termux.sh "$Z"
```

**注意**：`install-termux.sh` 不会被自己更新。每次换新包，必须先重新解压到 `~/.dbagent-boot`，
否则跑的还是旧脚本（旧脚本用 `pip install -r requirements.txt`，会卡在依赖解析）。

如果你已经手动装好依赖，可以跳过脚本里的包安装：

```bash
DBAGENT_SKIP_PKGS=1 bash ~/.dbagent-boot/dbagent/install-termux.sh "$Z"
```

非交互配置 Key：

```bash
DBAGENT_KEY=sk-xxx bash ~/.dbagent-boot/dbagent/install-termux.sh "$Z"
```

### 7. 启动

```bash
cd ~/dbagent && bash run.sh
```

正常启动后：

```
  DBAgent
  entry : package/main.py
  python: python3 (Python 3.x)
  cwd   : /data/data/com.termux/files/home/dbagent
🔑 已检测到 API Key，直接进入对话开发模式。
需求>
```

日常启动只需 `cd ~/dbagent && bash run.sh`；`/help` 看命令，`/quit` 退出。

---

## 本次安装踩到的坑（已修订）

### 1. 清华 PyPI 源缺包 → `from versions: none`

**现象**

```
ERROR: Could not find a version that satisfies the requirement python-docx>=1.1 (from versions: none)
ERROR: No matching distribution found for python-docx>=1.1
[错误] 核心依赖安装失败（可重跑本脚本）
```

**原因**：清华 PyPI 镜像上 `chardet`、`python-docx` 索引缺失。这两个包本身是纯 Python wheel，
官方源完全正常，不是 Termux 架构问题。

**处理**：执行第 3 节清掉 pip.conf，或用官方源显式安装：

```bash
pip install --no-deps -i https://pypi.org/simple chardet python-docx
```

### 2. pip 长时间无输出 → 以为卡死

**现象**：停在 `Requirement already satisfied: ...` 之后毫无反应。

**原因**：旧脚本用 `pip install -r requirements.txt` 一次性解析全部依赖。解析到 Pillow 这类
无预编译 wheel 的包时，pip 去下载源码并现场编译，这期间不打印任何内容。

**处理**：

- 先 `pkg install -y python-pillow` 用预编译版
- 或用 `--no-deps` 跳过依赖树解析
- 新脚本逐包安装并打印 `[3/10] Pillow>=10.0`，能直接看出卡在哪个包

### 3. `pkg install` 无输出 → 以为卡死

**现象**：脚本打印 `== 安装 Termux 预编译包 ==` 后长时间空白。

**原因**：旧脚本用 `pkg install ... >/dev/null 2>&1` 把下载进度全丢了。`python-pillow`
要下载几十 MB，Termux 网络下就是几分钟的空白。

**处理**：新脚本不再重定向输出。若确实要跳过这段，用 `DBAGENT_SKIP_PKGS=1`。

### 4. 脚本反复跑成旧版

**现象**：明明换了新包，报错格式还是旧的。

**原因**：`~/.dbagent-boot/dbagent/install-termux.sh` 是解压出来的副本，新 zip 不会替换它。
直接执行这个路径，跑的就是上次解压的旧脚本。

**处理**：换包时先 `rm -rf ~/.dbagent-boot` 再重新解压（见第 6 节）。

### 5. 生成 Excel 静默失败

**现象**：`整理成表格` 有 Markdown 表格，但没有生成 xlsx 文件，也没有任何报错。

**原因**：`openpyxl` 未安装时，早期代码 `except Exception: return None` 吞掉了错误。

**处理**：装 `openpyxl`；新版本会明确打印 `⚠️ 未安装 openpyxl，无法生成 Excel（pip install openpyxl）`。

---

## 常见问题

| 现象 | 处理 |
|---|---|
| `termux-change-repo` 里清华显示 bad | 正常，清华 Termux 源已下线，用 USTC |
| `Could not find a version ... (from versions: none)` | 清华 PyPI 源缺包，见第 3 节换官方源 |
| pip 停在 `Requirement already satisfied` 后不动 | 在编译 Pillow，先 `pkg install python-pillow`，或 `--no-deps` |
| `pkg install` 长时间无输出 | 在下载，等几分钟；确认是否该看进度而非卡死 |
| 换包后报错格式没变 | 跑的是 `~/.dbagent-boot` 里的旧脚本，重新解压 |
| `ModuleNotFoundError: requests` | `pip install --no-deps -i https://pypi.org/simple requests` |
| `unzip: cannot find ... dbagent-*.zip` | `find ~ /sdcard -iname "dbagent*.zip"` |
| 启动提示找不到 `package/main.py` | 没在项目根目录，先 `cd ~/dbagent` |
| pptx 技能报找不到模块 | `cd ~/dbagent/workspace/skills/pptx && npm install` |
| 技能执行报「沙箱内找不到脚本」 | 确认 `workspace/skills/` 完整解压 |
| `pkg install` 报锁错误 | 之前 Ctrl+C 留下的锁：`pkill -f apt && pkg update -y` |

---

## 环境说明

- Termux 无 root，自动使用**目录隔离沙箱**（`package/plat/termux/sandbox.py`），不依赖 chroot。
- 技能运行时用宿主 Python，工作目录锁定在 `$TMPDIR/dbagent-sandbox/work-<key>`，产出同步回 `workspace/data/work/`。
- API Key 不入包；按 `~/.dbagent/agent_coder.json` 或环境变量 `AGENT_API_KEY` 配置。
- 核心对话只依赖标准库；第三方包仅具体技能使用，缺哪个补哪个。
- 可选依赖（lxml / pdfplumber / cryptography）装不上不影响主流程，只影响对应技能。
