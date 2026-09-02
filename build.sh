#!/usr/bin/env bash
# ============================================================
# aiwindow 一键构建 rpm 安装包（v1.6.7 · 纯 GUI 桌面应用 · 智谱 GLM-4.7-Flash）
# ============================================================
# ★ v1.6.7：修复保存失败（/etc/aiwindow 权限残留不可读 bootstrap.key 导致
#   Permission denied）：_resolve_seed_file 增加 R_OK 可读校验自动回退随包种子、
#   _read_bootstrap_password 容错、set_api_key 自动生成新 bootstrap 兜底；
#   新增 get_cloud_status() 供设置页显示「当前模型连接状态」。
# ★ v1.6.6：修复模式下拉「设置…」进不去设置页；支持工程内预置种子
#   seed_key.enc / bootstrap.key（随 gui zip 分发，首次运行自动导入）。
# 本版为「纯 GUI 桌面应用版」：
#   - 只保留云端模式（智谱 GLM-4.7-Flash，默认
#     base_url=https://open.bigmodel.cn/api/paas/v4，model=glm-4.7-flash）
#   - 彻底移除本地模式：不再打包 llama-server / GGUF 模型 / local_init.py /
#     aiwindow-local-llm.service / vendor-llama / prebuilt，包内无本地模型推理。
#   - 纯 GUI 单进程：GTK 悬浮窗为唯一前台应用，HTTP 管理后台 / static 网页
#     全部移除；配置与密钥在悬浮窗「设置界面」直接读写 SQLite（复用 backend
#     模块），聊天为进程内直调 backend.handle_chat，无端口监听、无独立 HTTP 服务。
#   - backend.py 降级为进程内模块供 GUI import（配置 + AES 密钥 + 云端转发 +
#     日志），启动入口为 backend.py（初始化后拉起悬浮窗 GUI）或 floating_window.py。
#   - ★ v1.6.0 后台运行：关闭悬浮窗（delete-event / Alt+F4 / WM 关闭）仅隐藏
#     窗口、悬浮球后台驻留，不退出进程；托盘图标点击切换显隐、右键菜单可退出；
#     展开态「设置」页新增「退出应用」按钮（弹确认框，确认才真正退出进程）。
#   - ★ 开机自启动：systemd aiwindow-backend.service 由 rpm %post / deb postinst
#     systemctl enable（multi-user.target 开机拉起，无图形会话时仅初始化数据退出）；
#     桌面登录由 /etc/xdg/autostart/aiwindow-floating.desktop 启动悬浮窗 GUI。
#   - 全部 Python 依赖已内置为源码，构建过程 100% 离线，无需 pip。
#
# 技术栈：
#   - 进程内模块 backend.py     -> 仅 Python 标准库（sqlite3 + urllib 云端转发）
#   - 悬浮窗 floating_window.py -> GTK3（PyGObject），单窗口双形态一体化
#     （收缩态=仅悬浮球 / 展开态=圆球+聊天面板同一窗口；展开态内嵌「设置」页）
#
# ★ v1.6.0 新增后台运行 + 开机自启动：
#   关闭窗口不退出（隐藏驻留 + 托盘 + 设置页「退出应用」确认退出）；
#   自启动由 systemd enable + autostart 双通道保证（见上）。
#   ★ v1.5.0 改造为纯 GUI 桌面应用：移除 HTTP 管理后台 / /api/* / /static/* 网页，
#   悬浮窗展开态新增「设置」按钮进入设置界面（直读写 SQLite、密钥 AES 加密、
#   展示版本与智谱信息、调用日志列表）；聊天改为进程内直调 backend.handle_chat；
#   启动入口直接启 GUI，无端口监听、无独立 HTTP 服务；打包同步为纯 GUI 形态。
#   ★ v1.4.0 改造为纯云端单进程：删除本地 llama-server/GGUF/local-llm 服务
#   全部逻辑与打包内容；云端默认指向智谱 GLM-4.7-Flash；HTTP 后端与悬浮窗
#   GUI 合并单进程（GUI 线程内嵌）。
#
# 目标机/构建机硬依赖（几乎每台麒麟都自带）：
#     python3、openssl（出 rpm 还需 rpm-build）
#   （悬浮窗额外需要 GTK3 + PyGObject：openEuler/麒麟 sudo yum install gtk3
#     python3-gi cairo-gobject；缺失时悬浮窗不可用。）
#
# 用法：
#   # 方式一：提供默认云端 API 密钥（openssl AES 加密为种子随包，安装首启自动导入）
#   AIWINDOW_API_KEY="你的密钥" bash build.sh
#   或
#   bash build.sh "你的密钥"
#   # 方式二：不提供密钥，安装后到悬浮窗「设置界面」手动配置
#   bash build.sh
#
# 可选开关：
#   AIWINDOW_DEB=1 bash build.sh          # 额外输出 deb（需 dpkg-deb，缺失自动用纯 Python 打包器）
#   AIWINDOW_RPM=0 AIWINDOW_DEB=1 bash build.sh   # 仅输出 deb（不在 rpm 系机器时的备选）
#
# 产物： <工程根>/aiwindow-<版本>-1.<架构>.rpm    （默认主产物）
#        <工程根>/aiwindow_<版本>_<架构>.deb      （AIWINDOW_DEB=1 时）
# ============================================================
set -euo pipefail

PROJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJ_DIR"

VERSION="1.6.7"

# ★ v1.6.2 修复：say/ok/warn/die 四个函数与颜色变量提前定义，确保下面的
#   非 ASCII 路径检测块（调用 warn）能正常执行，避免含中文/非 ASCII 路径下
#   "warn: 未找到命令" 导致 set -euo pipefail 下构建中止。
C_Y="\033[1;33m"; C_G="\033[1;32m"; C_R="\033[1;31m"; C_N="\033[0m"
say(){ echo -e "${C_Y}[build]${C_N} $*"; }
ok(){ echo -e "${C_G}[OK]${C_N} $*"; }
warn(){ echo -e "${C_R}[WARN]${C_N} $*"; }
die(){ echo -e "${C_R}[ERROR]${C_N} $*" >&2; exit 1; }

# ★ v1.2.6：路径非 ASCII 检测。rpmbuild 的 brp-strip 等后处理脚本在 C locale 下
#   无法正确处理非 ASCII（如中文"下载"）构建路径，会因路径损坏报
#   "/usr/bin/strip: '...<乱码>.../backend.py': No such file" 导致 %install 失败。
#   这里仅作醒目提示（rpm 工作目录下方会自动切到纯英文临时目录，见 7.1）。
if printf '%s' "$PROJ_DIR" | LC_ALL=C grep -q '[^ -~]'; then
  warn "检测到工程路径含非 ASCII 字符：$PROJ_DIR"
  warn "  rpmbuild 的后处理脚本（brp-strip）在非 ASCII 路径下会损坏路径并失败，"
  warn "  本脚本已自动将 rpm 构建工作区切换到纯英文临时目录以规避；"
  warn "  如需彻底根治，建议把工程移动到纯英文路径（如 mv \"$PROJ_DIR\" /home/vmuser/aiwindow）后再构建。"
fi
PKG_NAME="aiwindow"

# ---------- 1. 架构检测 ----------
RAW_ARCH="$(uname -m)"
case "$RAW_ARCH" in
  loongarch64|loongson64)  ARCH="loongarch64" ;;
  aarch64|arm64)   ARCH="arm64" ;;
  x86_64|amd64)    ARCH="amd64" ;;
  i386|i486|i586|i686) ARCH="i386" ;;
  riscv64)         ARCH="riscv64" ;;
  *) die "暂不支持当前架构: $RAW_ARCH" ;;
esac
say "[1/6] 检测到架构: $RAW_ARCH  ->  安装包架构: $ARCH"

# ---------- 2. 出包模式开关（rpm 默认必出，deb 可选） ----------
MAKE_RPM=1
MAKE_DEB=0
[ "${AIWINDOW_RPM:-1}" = "0" ] && MAKE_RPM=0
[ "${AIWINDOW_DEB:-0}" = "1" ] && MAKE_DEB=1

# 硬依赖检查（python3 + openssl 必须）
command -v python3 >/dev/null 2>&1 || die "缺少 python3（openEuler/麒麟服务器版: sudo yum install python3；Debian/Ubuntu: sudo apt install python3）"
command -v openssl >/dev/null 2>&1 || die "缺少 openssl（openEuler/麒麟服务器版: sudo yum install openssl；Debian/Ubuntu: sudo apt install openssl）"
PYTHON_BIN="$(command -v python3)"
ok "python3: $PYTHON_BIN / openssl: 已就绪"

# rpm-build 检查：rpm 为默认主产物，缺失且未显式只要 deb 时直接中断并给安装指引
HAVE_RPMBUILD=0
if command -v rpmbuild >/dev/null 2>&1; then
  HAVE_RPMBUILD=1
elif [ "$MAKE_RPM" = "1" ] && [ "$MAKE_DEB" = "0" ]; then
  die "缺失 rpm-build：本版默认只输出 rpm，请先安装。"
fi

# GTK3 / PyGObject 检测（非致命：仅悬浮窗需要）
# v1.3.0 起悬浮窗为 GTK3 双形态一体化组件，需 python3-gi + gtk3。
if ! python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" >/dev/null 2>&1; then
  warn "未检测到 GTK3 / PyGObject：悬浮窗将不可用（后端/管理后台不受影响）。"
  warn "  openEuler/麒麟: sudo yum install gtk3 python3-gi cairo-gobject"
  warn "  Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-3.0"
fi

# dpkg-deb 非硬性依赖：缺失时自动降级用内置纯 Python 打包器 scripts/mkdeb.py
HAVE_DPKG_DEB=0
if [ "$MAKE_DEB" = "1" ] && command -v dpkg-deb >/dev/null 2>&1; then
  HAVE_DPKG_DEB=1
elif [ "$MAKE_DEB" = "1" ]; then
  say "未检测到 dpkg-deb，将使用内置纯 Python 打包器（scripts/mkdeb.py）出 deb"
fi

echo "==== 出包计划: rpm=${MAKE_RPM} / deb=${MAKE_DEB} ===="

# ---------- 3. 构建目录 ----------
BUILD_DIR="$PROJ_DIR/build"
STAGE="$BUILD_DIR/pkgroot"
rm -rf "$STAGE" 2>/dev/null || true
mkdir -p "$STAGE"

# ---------- 4. 生成默认密钥种子（openssl AES-256 加密，不落明文源码） ----------
say "[2/6] 生成密钥种子"
mkdir -p "$STAGE/etc/aiwindow"

API_KEY="${AIWINDOW_API_KEY:-}"
if [ -z "$API_KEY" ] && [ "$#" -ge 1 ]; then
  API_KEY="$1"
fi

if [ -n "$API_KEY" ]; then
  BOOTSTRAP_HEX="$(openssl rand -hex 32)"
  printf '%s' "$BOOTSTRAP_HEX" > "$STAGE/etc/aiwindow/bootstrap.key"
  PLAIN_TMP="$BUILD_DIR/.api_key.plain"
  printf '%s' "$API_KEY" > "$PLAIN_TMP"
  openssl enc -aes-256-cbc -a -A -salt -k "$BOOTSTRAP_HEX" \
    -in "$PLAIN_TMP" -out "$STAGE/etc/aiwindow/seed_key.enc"
  rm -f "$PLAIN_TMP"
  chmod 600 "$STAGE/etc/aiwindow/bootstrap.key" "$STAGE/etc/aiwindow/seed_key.enc"
  ok "默认云端密钥已用 openssl AES-256 加密 -> seed_key.enc (600)"
else
  openssl rand -hex 32 > "$STAGE/etc/aiwindow/bootstrap.key"
  chmod 600 "$STAGE/etc/aiwindow/bootstrap.key"
  say "未提供 AIWINDOW_API_KEY，跳过默认种子；安装后请到悬浮窗设置界面配置密钥。"
fi

# ---------- 5. 组装安装包目录树（rpm/deb 同源，纯文件分发） ----------
say "[3/6] 组装安装包目录树 -> $STAGE"
APP_DIR="$STAGE/usr/lib/aiwindow"
mkdir -p "$APP_DIR"

# 5.1 Python 源码（纯 GUI 桌面应用：backend.py 为进程内模块供 GUI import，
#     floating_window.py 为悬浮窗 GUI，启动入口 backend.py / floating_window.py）
cp "$PROJ_DIR/backend.py"         "$APP_DIR/backend.py"
cp "$PROJ_DIR/floating_window.py" "$APP_DIR/floating_window.py"
cp "$PROJ_DIR/crypto.py"          "$APP_DIR/crypto.py"
chmod 644 "$APP_DIR/backend.py" "$APP_DIR/floating_window.py" "$APP_DIR/crypto.py"

# 5.1.1 ★ v1.6.8 修复「开机自启后每次要重填密钥」：把配对的种子/主密钥同时
#   复制到 APP_DIR（/usr/lib/aiwindow，权限 644 全局可读）随 backend.py 分发。
#   桌面 autostart GUI 以登录用户身份运行，读不到 /etc/aiwindow/*（600 属主
#   aiwindow），此前会新生成一把与 seed_key.enc 不配对的 bootstrap.key 导致种子
#   解密失败、默认密钥导不进来。_resolve_seed_file 会回退 backend.py 同目录，
#   在此放一份「配对」的可读种子即可保证首启自动导入成功。
if [ -f "$STAGE/etc/aiwindow/seed_key.enc" ]; then
  cp "$STAGE/etc/aiwindow/seed_key.enc"  "$APP_DIR/seed_key.enc"
  cp "$STAGE/etc/aiwindow/bootstrap.key" "$APP_DIR/bootstrap.key"
  chmod 644 "$APP_DIR/seed_key.enc" "$APP_DIR/bootstrap.key"
  ok "配对的默认种子已随包分发到 $APP_DIR（GUI 用户可读，首启自动导入）"
fi

# 5.2 内置 Python 依赖（pynput / python-xlib / six 源码），剔除 dist-info 减体积
# vendor/ 为构建期可能缺失的目录：缺失时 %files 中的 __VENDOR__ 占位留空，
# 避免 rpmbuild 因 'File not found: vendor/' 报错（与 seed_key.enc 同类处理）。
VENDOR_FILES='/usr/lib/aiwindow/vendor/'
if [ -d "$PROJ_DIR/vendor" ]; then
  mkdir -p "$APP_DIR/vendor"
  find "$PROJ_DIR/vendor" -mindepth 1 -maxdepth 1 \
       ! -name '*.dist-info' -exec cp -r {} "$APP_DIR/vendor/" \;
  chmod -R a+rX "$APP_DIR/vendor"
  ok "已内置 Python 依赖 vendor/（pynput 全局热键）"
else
  VENDOR_FILES=''
  warn "未找到 vendor/ 目录，悬浮窗全局热键不可用（需在目标机 pip install pynput）"
fi

# 5.3 systemd 服务（唯一服务：aiwindow-backend.service，纯 GUI 应用形态；
#     无图形会话时仅初始化数据后退出，桌面登录后由 autostart 启动悬浮窗 GUI）
mkdir -p "$STAGE/lib/systemd/system"
sed -e 's/\r$//' -e "s|__PYTHON__|${PYTHON_BIN}|g" \
  "$PROJ_DIR/packaging/lib/systemd/system/aiwindow-backend.service" \
  > "$STAGE/lib/systemd/system/aiwindow-backend.service"
chmod 644 "$STAGE/lib/systemd/system/aiwindow-backend.service"

# 5.4 桌面登录自启（纯 GUI 桌面应用：启动 backend.py 即拉起悬浮窗 GUI；
#     无 HTTP 服务、无端口监听，唯一前台应用）
mkdir -p "$STAGE/etc/xdg/autostart"
sed -e 's/\r$//' -e "s|__PYTHON__|${PYTHON_BIN}|g" \
  "$PROJ_DIR/packaging/etc/xdg/autostart/aiwindow-floating.desktop" \
  > "$STAGE/etc/xdg/autostart/aiwindow-floating.desktop"
chmod 644 "$STAGE/etc/xdg/autostart/aiwindow-floating.desktop"

# 5.5 数据目录占位（安装时由 postinst/%post 创建并赋权）
mkdir -p "$STAGE/var/lib/aiwindow"

find "$STAGE" -type d -exec chmod 755 {} + 2>/dev/null || true

# ---------- 6. 打包 rpm（默认主产物） ----------
RPM_FILE=""
if [ "$MAKE_RPM" = "1" ]; then
  say "[4/6] 打包 rpm -> $PROJ_DIR/${PKG_NAME}-${VERSION}-1*.rpm"
  if [ "$HAVE_RPMBUILD" != "1" ]; then
    warn "未安装 rpmbuild，跳过 rpm 打包（请在 yum 系机器执行：sudo yum install -y rpm-build）。"
  else
    RPM_ARCH="$(rpmbuild --eval '%{_arch}' 2>/dev/null || echo "$ARCH")"
    # ★ v1.2.7：rpm 构建工作区自动选择"剩余空间最充足的纯英文目录"。
    #   先清理历史残留的 aiwindow-rpmbuild.*，再对候选目录（工程目录及其父级、
    #   /var/tmp、/tmp、$HOME）按可用空间排序，取最大且纯英文者作为工作区基目录；
    #   仍保证纯英文路径（规避 brp-strip），同时避免 /tmp 爆盘。产物最终复制回 $PROJ_DIR。
    # 1) 清理历史残留（仅脚本自建的同名临时目录，安全）
    for _b in /tmp /var/tmp "$PROJ_DIR" "$(dirname "$PROJ_DIR")" "$HOME"; do
      [ -d "$_b" ] || continue
      rm -rf "$_b"/aiwindow-rpmbuild.* 2>/dev/null || true
    done
    # 2) 收集纯英文且存在的候选目录
    CAND=()
    for _b in "$PROJ_DIR" "$(dirname "$PROJ_DIR")" /var/tmp /tmp "$HOME"; do
      [ -d "$_b" ] || continue
      if printf '%s' "$_b" | LC_ALL=C grep -q '[^ -~]'; then
        continue
      fi
      CAND+=("$_b")
    done
    # 3) 选可用空间（KB）最大者
    BEST=""
    BEST_KB=-1
    for _b in "${CAND[@]}"; do
      _kb=$(df -Pk "$_b" 2>/dev/null | awk 'NR==2{print $4}')
      if [ -n "$_kb" ] && [ "$_kb" -gt "$BEST_KB" ]; then
        BEST_KB=$_kb
        BEST="$_b"
      fi
    done
    RPMBUILD=""
    if [ -n "$BEST" ]; then
      RPMBUILD="$(mktemp -d "$BEST/aiwindow-rpmbuild.XXXXXX" 2>/dev/null || true)"
      if [ -n "$RPMBUILD" ]; then
        say "   rpm 工作区：$RPMBUILD（基目录 $BEST 可用 $((BEST_KB/1024/1024)) GB）"
      fi
    fi
    if [ -z "$RPMBUILD" ]; then
      RPMBUILD="$(mktemp -d /tmp/aiwindow-rpmbuild.XXXXXX 2>/dev/null || echo "$BUILD_DIR/rpmbuild")"
      warn "   rpm 工作区自动选择失败，回退到 /tmp/aiwindow-rpmbuild.*。"
    fi
    rm -rf "$RPMBUILD"
    mkdir -p "$RPMBUILD"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

    # 6.1 源码包：把已组装的目录树打成 aiwindow-<版本> 源码 tar.gz（rpm 源即根）
    # ★ 打包时用 -C "$SRC_STAGE" . 使 tar 顶层不含 aiwindow-<版本>/ 目录，
    #   配合 spec 的 "%setup -q -c -n aiwindow-%{version}" 避免顶层目录二次嵌套。
    SRC_STAGE="$BUILD_DIR/rpm-src/aiwindow-$VERSION"
    rm -rf "$BUILD_DIR/rpm-src"
    mkdir -p "$SRC_STAGE"
    cp -a "$STAGE"/. "$SRC_STAGE"/
    tar -czf "$RPMBUILD/SOURCES/aiwindow-$VERSION.tar.gz" \
      -C "$SRC_STAGE" .

    # 6.2 填充 spec 的版本号（纯 GUI 桌面应用版无本地模型/llama-server，FILES_EXTRA 恒为空）
    FILES_EXTRA=''
    SPEC_OUT="$RPMBUILD/SPECS/aiwindow.spec"
    if [ -f "$STAGE/etc/aiwindow/seed_key.enc" ]; then
      SEED_ENC='%config(noreplace) /etc/aiwindow/seed_key.enc'
    else
      SEED_ENC=''
    fi
    # ★ v1.6.8 随包分发到 APP_DIR 的配对种子（GUI 用户可读）也须纳入 %files；
    #   未提供 API_KEY（无种子）时占位留空，避免 rpmbuild 报 File not found。
    if [ -f "$APP_DIR/seed_key.enc" ]; then
      APP_SEED='/usr/lib/aiwindow/seed_key.enc
/usr/lib/aiwindow/bootstrap.key'
    else
      APP_SEED=''
    fi
    python3 - "$PROJ_DIR/packaging/rpm/aiwindow.spec.in" "$SPEC_OUT" "$VERSION" "$FILES_EXTRA" "$SEED_ENC" "$VENDOR_FILES" "$APP_SEED" <<'PY'
import sys
tmpl, out, version, files_extra, seed_enc, vendor, app_seed = (
    sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4],
    sys.argv[5], sys.argv[6], sys.argv[7])
repl = {'__VERSION__': version, '__FILES_EXTRA__': files_extra,
        '__SEED_ENC__': seed_enc, '__VENDOR__': vendor,
        '__APP_SEED__': app_seed}
res = []
for ln in open(tmpl, encoding='utf-8'):
    # ★ 注释行（含模板头部对占位符的说明）不参与替换：避免多行 __FILES_EXTRA__
    #   被拼入头部注释、把注释拆断后生成 spec 顶部出现裸文件路径
    #   （rpmbuild 会报 "行 N：未知标签：/usr/lib/aiwindow/llama-server"）。
    if ln.lstrip().startswith('#'):
        res.append(ln)
        continue
    for k, v in repl.items():
        ln = ln.replace(k, v)
    res.append(ln)
with open(out, 'w', encoding='utf-8', newline='\n') as f:
    f.write(''.join(res))
PY
    say "   使用 spec: $SPEC_OUT / 架构: $RPM_ARCH"

    # 6.3 rpmbuild 出二进制 rpm（无需 root）
    if ! rpmbuild --define "_topdir $RPMBUILD" -bb "$SPEC_OUT" >"$BUILD_DIR/rpmbuild.log" 2>&1; then
      die "rpmbuild 失败（详见 $BUILD_DIR/rpmbuild.log），请检查错误信息。"
    else
      RPM_FILE="$(find "$RPMBUILD/RPMS" -name 'aiwindow-*.rpm' -type f | head -1 || true)"
      if [ -n "$RPM_FILE" ]; then
        cp "$RPM_FILE" "$PROJ_DIR/"
        ok "rpm 构建完成：$(basename "$RPM_FILE")"
      else
        die "rpmbuild 成功但未在 $RPMBUILD/RPMS 找到 rpm 产物。"
      fi
    fi
  fi
fi

# ---------- 7. 可选·打包 deb（AIWINDOW_DEB=1） ----------
DEB_FILE=""
if [ "$MAKE_DEB" = "1" ]; then
  say "[5/6] 打包 deb (可选)"
  DROOT="$BUILD_DIR/debroot"
  rm -rf "$DROOT"
  cp -a "$STAGE" "$DROOT"
  # 7.1 DEBIAN 维护脚本 / control（版本号与架构在构建期填充）
  mkdir -p "$DROOT/DEBIAN"
  sed 's/\r$//' "$PROJ_DIR/packaging/DEBIAN/conffiles" > "$DROOT/DEBIAN/conffiles"
  sed 's/\r$//' "$PROJ_DIR/packaging/DEBIAN/postinst"   > "$DROOT/DEBIAN/postinst"
  sed 's/\r$//' "$PROJ_DIR/packaging/DEBIAN/postrm"     > "$DROOT/DEBIAN/postrm"
  sed "s/__ARCH__/${ARCH}/g; s/__VERSION__/${VERSION}/g" "$PROJ_DIR/packaging/DEBIAN/control" \
    | sed 's/\r$//' > "$DROOT/DEBIAN/control"
  chmod 755 "$DROOT/DEBIAN/postinst" "$DROOT/DEBIAN/postrm"
  chmod 644 "$DROOT/DEBIAN/conffiles" "$DROOT/DEBIAN/control"
  DEB_FILE="$PROJ_DIR/${PKG_NAME}_${VERSION}_${ARCH}.deb"
  if [ "$HAVE_DPKG_DEB" = "1" ]; then
    dpkg-deb --root-owner-group --build "$DROOT" "$DEB_FILE"
  else
    python3 "$PROJ_DIR/scripts/mkdeb.py" "$DROOT" "$DEB_FILE" \
      || die "内置 Python 打包失败"
  fi
  ok "deb 构建完成：$DEB_FILE"
fi

echo ""
echo "================ 使用方法（纯 GUI 桌面应用 · 智谱 GLM-4.7-Flash） ================"
if [ -n "$RPM_FILE" ]; then
  echo "  # 安装（openEuler / 麒麟服务器版等 yum 系）"
  echo "  sudo rpm -ivh ${PKG_NAME}-${VERSION}-1.${RPM_ARCH}.rpm"
  echo "  # 升级        sudo rpm -Uvh ${PKG_NAME}-${VERSION}-1.${RPM_ARCH}.rpm"
else
  echo "  # 安装（Debian/Ubuntu 系）"
  echo "  sudo dpkg -i ${PKG_NAME}_${VERSION}_${ARCH}.deb"
  echo "  # 若提示依赖缺失（一般不需要），执行：sudo apt -f install"
fi
echo ""
echo "  # 验证服务（systemd 在无图形会话时仅初始化数据；桌面 GUI 由 autostart 启动）"
echo "  systemctl status aiwindow-backend"
echo ""
echo "  # 悬浮窗（唯一前台应用）：桌面登录后自动启动（收缩态悬浮球），"
echo "  #          点击圆球展开聊天面板；展开态头部「设置」按钮进入设置界面"
echo "  #          （配置密钥/base_url/model/timeout、查看版本与智谱信息、调用日志）。"
echo "  # 无 HTTP 管理后台、无端口监听。"
echo ""
echo "  # 默认云端：智谱 GLM-4.7-Flash（https://open.bigmodel.cn/api/paas/v4）"
echo "  # 安装时若已用 AIWINDOW_API_KEY 预置密钥，首启自动导入；否则到设置界面填写。"
echo ""
if [ -n "$RPM_FILE" ]; then
  echo "  # 卸载（rpm）sudo rpm -e aiwindow"
  echo "  #            /etc/aiwindow 密钥与 /var/lib/aiwindow 数据如需保留，请先备份)"
else
  echo "  # 卸载（deb，保留数据）sudo dpkg -r ${PKG_NAME}"
  echo "  # 彻底清除 data 与用户 sudo dpkg -P ${PKG_NAME}"
fi
echo ""
echo "=========================================="
