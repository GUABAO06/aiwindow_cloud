#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backend.py — aiwindow 纯 GUI 桌面应用进程内模块（纯 Python 标准库，零第三方依赖）
=========================================================
- 不再提供 HTTP 服务：本文件仅作为进程内模块供悬浮窗 GUI（floating_window.py）
  import 使用，提供配置持久化 / AES 密钥管理 / 云端转发 / 调用日志等能力。
- SQLite 单文件存储（settings + 极简日志），路径 /var/lib/aiwindow/aiwindow.db
- 纯云端模式（智谱 GLM-4.7-Flash 默认）：
    base_url = https://open.bigmodel.cn/api/paas/v4
    model    = glm-4.7-flash
  请求转发给智谱 OpenAI 兼容 /chat/completions，需 API-Key 与联网
- 云端 API 密钥 AES 加密后写入 SQLite，任何时刻不落明文
  首次运行自动用 /etc/aiwindow/bootstrap.key 解密 seed_key.enc 导入默认密钥
- 聊天由 GUI 进程内直调 handle_chat() 完成，不经过任何本地 HTTP 服务；
  启动入口为 floating_window.py（GTK 主循环），无端口监听、无独立 HTTP 服务。
- ★ v1.6.7 修复保存失败 + 连接状态显示：_resolve_seed_file 对候选文件增加
  os.access(R_OK) 可读性校验，跳过不可读的 /etc/aiwindow 权限残留文件，自动回退
  backend.py 同目录随包分发的可读 seed/bootstrap；_read_bootstrap_password 增加
  try/except 容错（PermissionError/OSError 返回 None）；set_api_key 在 bootstrap
  密码缺失/不可读时自动在可写目录（ETC_DIR→~/.config/aiwindow→DATA_DIR）生成新
  bootstrap.key 兜底，保证保存 100% 成功；新增 get_cloud_status() 供设置页显示
  「当前模型连接状态」（绿=已连接 HTTP200 / 红=鉴权失败或网络异常 / 灰=未配置密钥）。
- ★ v1.6.4 悬浮窗优化：设置入口移入模式下拉列表（末尾「设置…」项），
  移除展开态头部「设置」按钮；窗口任务栏隐藏强化（type_hint UTILITY +
  映射后延迟重设 skip 提示）。
- ★ v1.6.3 悬浮窗优化：窗口不在任务栏显示、设置按钮紧挨收起、移除悬浮球右键菜单（退出走托盘/设置页）
- ★ v1.6.2 构建修复：build.sh 将 say/ok/warn/die 四个函数与颜色变量提前
- ★ v1.6.1 悬浮球右键菜单：右键点击悬浮球直接弹出菜单，含
  「显示/隐藏聊天面板」与「退出 aiwindow」（复用确认框），
  为最直接的界面退出入口（银河麒麟等无系统托盘的桌面亦可退出）。
- ★ v1.6.0 后台运行：关闭悬浮窗（delete-event / Alt+F4 / WM 关闭）仅隐藏窗口、
  悬浮球后台驻留不退出进程；托盘图标与「设置」页「退出应用」按钮（弹确认框）
  提供真正退出进程的入口。
- ★ v1.6.0 开机自启动：systemd aiwindow-backend.service（rpm %post / deb postinst
  已 systemctl enable，multi-user.target）配合 /etc/xdg/autostart/
  aiwindow-floating.desktop 桌面登录自启，开机即拉起悬浮窗。
- 转发 OpenAI 兼容 /chat/completions，纯标准库 urllib（不引入 httpx/requests）
- 日志仅存「时间 / 状态 / 提问摘要（前几十字）」，不保存完整对话
- 极简输出，无外部依赖

对外提供（GUI 设置界面 / 聊天直调复用）：
  get_setting / set_setting                配置读写（SQLite）
  get_api_key / set_api_key                密钥 AES 加密存取（不落明文）
  get_timeout / get_cloud_base_url / get_cloud_model   生效配置读取
  list_logs / clear_logs / add_log         调用日志
  handle_chat(payload)                     进程内聊天转发（OpenAI messages 结构）
  handle_set_config(body)                  配置写入（key/base_url/model/timeout）
  ensure_default_key_from_seed             首次运行自动导入安装种子密钥
  ensure_ready()                           初始化数据库 + 导入默认密钥
"""

import os
import sys
import json
import time
import sqlite3
import threading
import urllib.request
import urllib.error

# ---------- 允许直接以源码方式运行（import crypto） ----------
try:
    import crypto
except ImportError:
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    import crypto

# ---------- 路径（环境变量优先；否则锁定登录用户家目录下稳定路径） ----------
# ★ v1.6.8 修复「开机自启后配置丢失」根因：
#   旧逻辑下 systemd 服务（User=aiwindow，注入 AIWINDOW_DATA=/var/lib/aiwindow）
#   与桌面 autostart GUI（当前登录用户、不注入环境变量）会落到两份不同的数据库，
#   且 /var/lib/aiwindow 属主 aiwindow 权限 750，登录用户不可写时旧逻辑再降级到
#   ~/.local/share/aiwindow —— 导致「上次填的配置读不到 / 每次开机要重填」。
#   现改为：显式环境变量始终优先且不降级（rpm systemd 场景行为不变）；未注入
#   环境变量时（桌面 autostart GUI 真正持久使用配置的进程）一律锁定到
#   ~/.config/aiwindow —— 单一、稳定、当前登录用户必可写，保证每次开机读到同一
#   份 SQLite 配置库，不再随「哪个目录恰好可写」漂移。
def _resolve_paths():
    data_dir = os.environ.get("AIWINDOW_DATA")
    etc_dir = os.environ.get("AIWINDOW_ETC")

    # 数据目录：显式 AIWINDOW_DATA 优先且不降级；否则锁定 ~/.config/aiwindow。
    # 仅当家目录不可写这种极端情况才回退到默认系统目录（兜底，正常不触发）。
    if not data_dir:
        home_dir = os.path.expanduser("~/.config/aiwindow")
        try:
            os.makedirs(home_dir, exist_ok=True)
            if not os.access(home_dir, os.W_OK):
                raise OSError("home config dir not writable")
            data_dir = home_dir
        except OSError:
            data_dir = "/var/lib/aiwindow"

    # 配置目录：显式 AIWINDOW_ETC 优先；否则与数据目录同锁 ~/.config/aiwindow，
    # 与 seed/bootstrap 随包分发（backend.py 同目录）配合，GUI 用户必可读。
    if not etc_dir:
        etc_dir = os.path.expanduser("~/.config/aiwindow")

    return data_dir, etc_dir


DATA_DIR, ETC_DIR = _resolve_paths()
DB_PATH = os.path.join(DATA_DIR, "aiwindow.db")

# 旧版本可能把配置写在 ~/.local/share/aiwindow（旧降级路径）或 /var/lib/aiwindow。
# 首启若新库尚无任何配置而旧库存在，则一次性迁移，避免升级后「配置像丢了」。
_LEGACY_DB_PATHS = [
    os.path.join(
        os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
        "aiwindow", "aiwindow.db"),
    "/var/lib/aiwindow/aiwindow.db",
]


def _resolve_seed_file(name):
    """种子文件定位：优先 ETC_DIR；候选文件需存在且可读（os.isfile +
    os.access R_OK）才采纳，跳过 root 600 等不可读的 /etc/aiwindow 权限残留文件，
    自动回退到 backend.py 同目录随包分发的可读 seed/bootstrap。
    rpm/deb 安装场景 ETC_DIR（/etc/aiwindow）始终存在可读种子，行为不变。"""
    for base in (ETC_DIR, os.path.dirname(os.path.abspath(__file__))):
        p = os.path.join(base, name)
        if os.path.isfile(p) and os.access(p, os.R_OK):
            return p
    return os.path.join(ETC_DIR, name)


SEED_FILE = _resolve_seed_file("seed_key.enc")
BOOTSTRAP_FILE = _resolve_seed_file("bootstrap.key")

APP_VERSION = "1.6.8"
LOG_KEEP = 500          # 日志最多保留条数，防无限膨胀
DEFAULT_TIMEOUT = 60    # 云端转发超时默认值（秒），可在设置界面「超时时间」中调整

# ---------- 纯云端默认配置（智谱 GLM-4.7-Flash） ----------
# 默认模型为智谱官方免费模型 glm-4.7-flash（open.bigmodel.cn 免费额度）。
# 注：若遇到 HTTP 429「该模型当前访问量过大」为平台侧临时过载，稍后重试即可，
# 与本应用无关；HTTP 403「您无权访问」多为密钥无权调用该模型或密钥带空白污染，
# 已在 set_api_key/get_api_key 统一 strip，并在 403 文案中回显平台原始提示。
DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4.7-flash"
CLOUD_PROBE_TIMEOUT = 5   # 云端连通性探测超时（秒）

# ★ v1.6.8 通用模型配置：预置多平台快捷模板（OpenAI 兼容 /chat/completions）。
#   供设置界面「平台」下拉快速填充 base_url 与常见 model，减少重复手填；
#   任何未列出的平台可选「自定义」手动填写。key=平台显示名，
#   值 = (base_url, 默认 model, 平台标识 provider_id)。
CLOUD_PRESETS = [
    ("智谱 GLM",   "https://open.bigmodel.cn/api/paas/v4", "glm-4.7-flash",       "zhipu"),
    ("OpenAI",     "https://api.openai.com/v1",            "gpt-4o-mini",         "openai"),
    ("DeepSeek",   "https://api.deepseek.com/v1",          "deepseek-chat",       "deepseek"),
    ("通义千问",   "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus", "qwen"),
    ("Kimi 月之暗面", "https://api.moonshot.cn/v1",         "moonshot-v1-8k",      "moonshot"),
    ("硅基流动",   "https://api.siliconflow.cn/v1",         "Qwen/Qwen2.5-7B-Instruct", "siliconflow"),
    ("Ollama 本地", "http://localhost:11434/v1",            "qwen2.5",             "ollama"),
    ("自定义",     "",                                      "",                    "custom"),
]


def _is_zhipu(base_url):
    """判断是否为智谱平台（仅智谱支持 thinking.type=enabled 深度思考私有字段）。
    以 base_url 域名包含 bigmodel.cn 判定，避免把私有字段发给其它平台致 400。"""
    return "bigmodel.cn" in (base_url or "").lower()

_DB_LOCK = threading.Lock()

# ============ 数据库（SQLite 单文件） ============
def _conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        os.chmod(DATA_DIR, 0o750)
    except OSError:
        pass
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_db():
    with _DB_LOCK:
        conn = _conn()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS settings ("
                " key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS logs ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " ts INTEGER NOT NULL, status TEXT NOT NULL, summary TEXT NOT NULL)")
            conn.commit()
        finally:
            conn.close()


def _current_db_has_settings():
    """当前库是否已有任何配置项（用于判断是否需要从旧库迁移）。"""
    try:
        conn = _conn()
        try:
            row = conn.execute("SELECT COUNT(*) FROM settings").fetchone()
            return bool(row and row[0])
        finally:
            conn.close()
    except Exception:
        return False


def _migrate_from_legacy_db():
    """★ v1.6.8 升级平滑迁移：新库（~/.config/aiwindow）尚无任何配置，而旧版本
    降级路径（~/.local/share/aiwindow 或 /var/lib/aiwindow）存在含配置的库时，
    把 settings 表整表拷入新库。仅在新库为空时执行一次，绝不覆盖已有配置；
    任何异常都吞掉（迁移失败不影响主流程，用户重填即可）。"""
    if _current_db_has_settings():
        return False
    for legacy in _LEGACY_DB_PATHS:
        try:
            if os.path.abspath(legacy) == os.path.abspath(DB_PATH):
                continue
            if not (os.path.isfile(legacy) and os.access(legacy, os.R_OK)):
                continue
            src = sqlite3.connect(legacy, timeout=5)
            try:
                rows = src.execute("SELECT key,value FROM settings").fetchall()
            finally:
                src.close()
            if not rows:
                continue
            for k, v in rows:
                set_setting(k, v)
            add_log("migrate-ok",
                    "已从旧数据库迁移 %d 项配置：%s" % (len(rows), legacy))
            return True
        except Exception:
            continue
    return False


# ============ 设置读写 ============
def get_setting(key):
    conn = _conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def set_setting(key, value):
    with _DB_LOCK:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))
            conn.commit()
        finally:
            conn.close()


def get_timeout():
    """读取后台配置的转发超时，取 5~300 秒内有效值，非法或未配置回退默认。"""
    try:
        t = int(get_setting("timeout") or "0")
    except (TypeError, ValueError):
        t = 0
    return t if 5 <= t <= 300 else DEFAULT_TIMEOUT


def get_cloud_base_url():
    """云端服务地址：后台配置优先，否则回退智谱默认。"""
    v = (get_setting("base_url") or "").strip()
    return v or DEFAULT_BASE_URL


def get_cloud_model():
    """云端模型名：后台配置优先，否则回退智谱默认。"""
    v = (get_setting("model") or "").strip()
    return v or DEFAULT_MODEL


# ============ 密钥管理：AES 加密存储，不落明文 ============
def _read_bootstrap_password():
    """读取主密钥 bootstrap.key（纯文本随机hex，权限600）。
    文件不存在或不可读（PermissionError/其它 OSError）时返回 None 而非抛异常。"""
    if not os.path.isfile(BOOTSTRAP_FILE):
        return None
    try:
        with open(BOOTSTRAP_FILE, "rb") as f:
            return f.read().decode("utf-8", "replace").strip()
    except OSError:
        return None


def _bootstrap_candidates():
    """bootstrap.key 兜底目录（去重，保序）：ETC_DIR → ~/.config/aiwindow → DATA_DIR。"""
    dirs = []
    for d in (ETC_DIR,
              os.path.expanduser("~/.config/aiwindow"),
              DATA_DIR):
        if d not in dirs:
            dirs.append(d)
    return dirs


def _ensure_bootstrap_password():
    """确保存在可读的主密钥 bootstrap.key。
    1) 现有 BOOTSTRAP_FILE 可读则直接返回其密码；
    2) 否则在候选目录查找已存在的可读 bootstrap.key（可能与旧种子配对）；
    3) 仍无则按 ETC_DIR→~/.config/aiwindow→DATA_DIR 顺序在可写目录生成新的
       bootstrap.key（openssl rand 随机 hex），并更新模块级 BOOTSTRAP_FILE。
    全部失败返回 None（此时 set_api_key 才会报错；正常环境不会发生）。"""
    global BOOTSTRAP_FILE
    pw = _read_bootstrap_password()
    if pw:
        return pw
    for d in _bootstrap_candidates():
        if d == os.path.dirname(BOOTSTRAP_FILE):
            continue  # 当前 BOOTSTRAP_FILE 已确认不可读/不存在，跳过重复候选
        p = os.path.join(d, "bootstrap.key")
        if os.path.isfile(p) and os.access(p, os.R_OK):
            try:
                with open(p, "rb") as f:
                    pw2 = f.read().decode("utf-8", "replace").strip()
                if pw2:
                    BOOTSTRAP_FILE = p
                    return pw2
            except OSError:
                continue
    for d in _bootstrap_candidates():
        try:
            os.makedirs(d, exist_ok=True)
            if not os.access(d, os.W_OK):
                continue
            new_pw = crypto.gen_password(32)
            path = os.path.join(d, "bootstrap.key")
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_pw)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            BOOTSTRAP_FILE = path
            return new_pw
        except Exception:
            continue
    return None


def get_api_key():
    """从 SQLite 读取加密密钥并解密。失败/未配置返回 None。
    ★ v1.6.8：返回前 strip()，兼容本次修复前已存入的带空白旧密钥，
    确保 Authorization 头永远是纯净 token。"""
    enc = get_setting("api_key_enc")
    if not enc:
        return None
    pw = _read_bootstrap_password()
    if not pw:
        return None
    try:
        key = crypto.aes_decrypt_text(enc, pw)
        return key.strip() if key else key
    except Exception:
        return None


def set_api_key(key):
    """把 API 密钥 AES 加密后写入 SQLite（明文只存在于内存变量中）。
    ★ v1.6.8：写入前 strip() 去除首尾空白/换行——从网页或文档复制密钥常带
    尾部空格或换行，会导致 urllib「Invalid header value」或平台鉴权异常（部分
    端点 401/403），且极难排查。此处统一清洗，保证存进去的就是纯净密钥。
    bootstrap 密码缺失/不可读时由 _ensure_bootstrap_password 自动在可写目录
    （ETC_DIR→~/.config/aiwindow→DATA_DIR）生成新 bootstrap.key 兜底，
    不再直接抛 Permission denied 导致保存失败。"""
    key = (key or "").strip()
    pw = _ensure_bootstrap_password()
    if not pw:
        raise RuntimeError(
            "无法生成主密钥 bootstrap.key"
            "（ETC_DIR / ~/.config/aiwindow / DATA_DIR 均不可写）")
    enc = crypto.aes_encrypt_text(key, pw)
    set_setting("api_key_enc", enc)


def has_api_key():
    return bool(get_api_key())


def ensure_default_key_from_seed():
    """首次运行：若数据库中尚未保存密钥且存在可读种子，自动导入默认密钥，
    并预置智谱默认 base_url / model。
    种子/主密钥缺失或不可读时：SEED_FILE 可读而 bootstrap 缺失/不可读时，
    由 _ensure_bootstrap_password 自动在可写目录生成新 bootstrap 兜底（若种子
    本身是用旧主密钥加密的，则解密失败会落到 init-fail 日志，不污染数据）。"""
    if get_setting("api_key_enc"):
        return False
    if not (os.path.isfile(SEED_FILE) and os.access(SEED_FILE, os.R_OK)):
        return False
    pw = _ensure_bootstrap_password()
    if not pw:
        return False
    try:
        with open(SEED_FILE, "rb") as f:
            data_b64 = f.read().decode("utf-8", "replace").strip()
        key = crypto.aes_decrypt_text(data_b64, pw)
        set_api_key(key)
        if not get_setting("base_url"):
            set_setting("base_url", DEFAULT_BASE_URL)
        if not get_setting("model"):
            set_setting("model", DEFAULT_MODEL)
        add_log("init-ok", "已从安装种子自动导入默认云端密钥（智谱 GLM-4.7-Flash）")
        return True
    except Exception as e:
        add_log("init-fail", "默认密钥导入失败: %s" % str(e)[:80])
        return False


# ============ 极简日志 ============
def add_log(status, summary, when=None):
    try:
        with _DB_LOCK:
            conn = _conn()
            try:
                conn.execute(
                    "INSERT INTO logs(ts,status,summary) VALUES(?,?,?)",
                    (int(when) if when else int(time.time()),
                     str(status)[:16], str(summary)[:200]))
                conn.execute(
                    "DELETE FROM logs WHERE id NOT IN "
                    "(SELECT id FROM logs ORDER BY id DESC LIMIT ?)", (LOG_KEEP,))
                conn.commit()
            finally:
                conn.close()
    except Exception:
        pass  # 日志失败不影响主流程


def list_logs(limit=100):
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT ts,status,summary FROM logs ORDER BY id DESC LIMIT ?",
            (int(limit),)).fetchall()
        return [{"ts": r[0], "status": r[1], "summary": r[2]} for r in rows]
    finally:
        conn.close()


def clear_logs():
    with _DB_LOCK:
        conn = _conn()
        try:
            conn.execute("DELETE FROM logs")
            conn.commit()
        finally:
            conn.close()


# ============ 云端转发（纯标准库 urllib） ============
def _extract_server_msg(data):
    """从平台错误响应体里提取可读错误信息（OpenAI/智谱 均为 {"error":{...}}）。"""
    try:
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                return str(err.get("message") or err.get("code") or "").strip()
            if isinstance(err, str):
                return err.strip()
            if data.get("message"):
                return str(data["message"]).strip()
    except Exception:
        pass
    return ""


def _friendly_cloud_error(status, server_msg=""):
    """把 HTTP 状态码翻译为用户可读文案；附带平台原始错误信息（若有），
    便于区分「密钥无权访问某模型」「密钥过期」「额度用尽」等不同根因。"""
    tail = ("（平台提示：%s）" % server_msg) if server_msg else ""
    if status == 401:
        return "云端鉴权失败（HTTP 401）：API 密钥无效或已过期，请在设置界面重新配置密钥。" + tail
    if status == 403:
        return ("云端无权访问（HTTP 403）：当前 API 密钥无权调用该模型，"
                "请确认密钥所属账户已开通对应模型，或在设置界面更换模型/密钥。" + tail)
    if status == 429:
        return "云端限流（HTTP 429）：请求过于频繁或额度用尽，请稍后再试。" + tail
    if status >= 500:
        return "云端服务暂时不可用（HTTP %d），请稍后重试。" % status
    return "云端返回错误（HTTP %d），请检查请求内容。" % status


def _forward_chat_completions(payload, base_url, api_key=None):
    """OpenAI 兼容 /chat/completions 纯转发。
    api_key 为空则不带 Authorization 头。返回 (status, obj)。
    连接失败抛 RuntimeError。"""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=get_timeout()) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            err = {}
        return e.code, err
    except urllib.error.URLError as e:
        raise RuntimeError("无法连接到服务（网络异常：%s）" % e.reason)


def _probe_chat_endpoint():
    """★ v1.6.8 降级探测：部分平台无 GET /models 端点（404/405）或格式不兼容。
    此时用最小 /chat/completions 请求探测连通性（max_tokens=1）。
    返回 (ok, detail, http_status)。只要返回 200 即视为已连接；4xx 鉴权类
    仍按错误处理。"""
    base_url = get_cloud_base_url()
    model = get_cloud_model()
    probe_payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    if _is_zhipu(base_url):
        probe_payload["thinking"] = {"type": "disabled"}
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json",
               "Authorization": "Bearer " + get_api_key()}
    req = urllib.request.Request(
        url, data=json.dumps(probe_payload).encode("utf-8"),
        headers=headers, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=CLOUD_PROBE_TIMEOUT) as resp:
            ms = int((time.time() - t0) * 1000)
            if resp.status == 200:
                return True, "连接成功（HTTP 200，%d ms）" % ms, 200
            return False, "服务异常（HTTP %d，%d ms）" % (resp.status, ms), resp.status
    except urllib.error.HTTPError as e:
        ms = int((time.time() - t0) * 1000)
        hint = "鉴权失败：API-Key 无效或已过期" if e.code in (401, 403) \
            else "限流或额度用尽" if e.code == 429 \
            else "服务错误"
        return False, "%s（HTTP %d，%d ms）" % (hint, e.code, ms), e.code
    except Exception as e:
        return False, "无法连接：%s" % str(e)[:80], None


def _cloud_probe():
    """探测云端服务是否可连（通用，OpenAI 兼容各平台）。
    优先 GET {base_url}/models；该端点不存在（404/405）时自动降级用最小
    /chat/completions 探测，兼容无 /models 端点的平台（如部分自建网关）。
    返回 (ok, detail, http_status)。连接失败 http_status 为 None。"""
    base_url = get_cloud_base_url()
    if not has_api_key():
        return False, "未配置 API-Key", None
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": "Bearer " + get_api_key()}
    req = urllib.request.Request(url, headers=headers, method="GET")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=CLOUD_PROBE_TIMEOUT) as resp:
            ms = int((time.time() - t0) * 1000)
            if resp.status == 200:
                return True, "连接成功（HTTP 200，%d ms）" % ms, 200
            return False, "服务异常（HTTP %d，%d ms）" % (resp.status, ms), resp.status
    except urllib.error.HTTPError as e:
        # /models 端点不存在（部分平台）：降级用 /chat/completions 探测
        if e.code in (404, 405):
            return _probe_chat_endpoint()
        ms = int((time.time() - t0) * 1000)
        hint = "鉴权失败：API-Key 无效或已过期" if e.code in (401, 403) \
            else "限流或额度用尽" if e.code == 429 \
            else "服务错误"
        return False, "%s（HTTP %d，%d ms）" % (hint, e.code, ms), e.code
    except Exception as e:
        return False, "无法连接：%s" % str(e)[:80], None


# 云端连通性探测短时缓存（TTL 5s），配置变更时由 handle_set_config 主动清空
_CLOUD_PROBE_CACHE = {"ts": 0.0, "result": None}
_CLOUD_PROBE_TTL = 5.0


def _cloud_probe_cached():
    now = time.time()
    if now - _CLOUD_PROBE_CACHE["ts"] < _CLOUD_PROBE_TTL:
        return _CLOUD_PROBE_CACHE["result"]
    r = _cloud_probe()
    _CLOUD_PROBE_CACHE.update(ts=now, result=r)
    return r


def get_cloud_status():
    """当前云端连接状态（复用 _cloud_probe_cached，TTL 5s）。
    返回 (status_text, level)，level 供设置页着色：ok=绿 / bad=红 / gray=灰。
    映射：未配置密钥→「未配置密钥」；连接成功(HTTP200)→「已连接（HTTP 200）」；
    401/403→「鉴权失败：Key 无效或已过期」；429→「限流或额度用尽」；
    其它→「网络/服务异常」。"""
    if not has_api_key():
        return "未配置密钥", "gray"
    ok, detail, status = _cloud_probe_cached()
    if ok and status == 200:
        return "已连接（HTTP 200）", "ok"
    if status in (401, 403):
        return "鉴权失败：Key 无效或已过期", "bad"
    if status == 429:
        return "限流或额度用尽", "bad"
    return "网络/服务异常：%s" % detail, "bad"


def _pick_summary(messages):
    """从 messages 中提取最后一条 user 内容，仅取前 40 字作日志摘要。"""
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            txt = str(m.get("content", "") or "")
            txt = " ".join(txt.split())
            return txt[:40] or "(空提问)"
    return "(无文本提问)"


def handle_chat(payload):
    """处理一次划词/对话请求。纯云端转发到智谱 GLM-4.7-Flash。
    返回 (status, response_dict)。"""
    messages = payload.get("messages") or []
    summary = _pick_summary(messages)
    return _handle_cloud_chat(payload, summary)


def _handle_cloud_chat(payload, summary):
    """云端转发模式：OpenAI 兼容 /chat/completions 通用转发，需 API-Key + base_url。
    ★ v1.6.8 通用化：仅智谱平台下发 thinking 私有字段，其它平台（OpenAI /
    DeepSeek / 通义 / Kimi / Ollama 等）只发标准字段，避免私有字段导致 400。"""
    api_key = get_api_key()
    if not api_key:
        add_log("chat-fail", summary + " | 未配置密钥")
        return 400, {"ok": False,
                     "error": "尚未配置云端 API 密钥，请在悬浮窗设置界面配置后使用。"}

    base_url = get_cloud_base_url()
    # 模型名优先级：请求体显式指定 > 后台配置的模型名称 > 默认
    model = str(payload.get("model") or "").strip() \
        or get_cloud_model()
    # 标准 OpenAI 兼容字段，所有平台通用
    outgoing = {
        "model": model,
        "messages": payload.get("messages") or [],
        "temperature": payload.get("temperature", 0.7),
        "max_tokens": int(payload.get("max_tokens") or 1024),
        "stream": bool(payload.get("stream", False)),
    }
    # ★ v1.6.8 私有字段按平台条件下发：
    #   - 请求体显式给了 thinking：无条件透传（调用方自负）。
    #   - 未显式指定且目标为智谱：默认启用深度思考 thinking.type=enabled。
    #   - 未显式指定且目标为其它平台：不加 thinking，保持标准请求体。
    if payload.get("thinking"):
        outgoing["thinking"] = payload["thinking"]
    elif _is_zhipu(base_url):
        outgoing["thinking"] = {"type": "enabled"}

    try:
        status, data = _forward_chat_completions(outgoing, base_url, api_key)
    except RuntimeError as e:
        add_log("chat-fail", summary + " | " + str(e)[:60])
        return 502, {"ok": False, "error": str(e)}
    except Exception as e:
        add_log("chat-fail", summary + " | 请求异常")
        return 500, {"ok": False, "error": "请求处理异常：%s" % e}

    if status != 200:
        server_msg = _extract_server_msg(data)
        add_log("chat-fail", "云端状态码 %s | %s%s"
                % (status, summary, ("｜" + server_msg) if server_msg else ""))
        return 502, {"ok": False,
                     "error": _friendly_cloud_error(status, server_msg),
                     "status": status}

    add_log("chat-ok", summary)
    return 200, data


def handle_set_config(body):
    """写入配置。body 为 dict。返回 (status, dict)。"""
    key = str(body.get("key") or "").strip()
    base_url = str(body.get("base_url") or "").strip()
    model = str(body.get("model") or "").strip()
    timeout = body.get("timeout")
    if not (key or base_url or model or timeout is not None):
        return 400, {"ok": False,
                     "msg": "请至少填写密钥、服务地址、模型名称或超时时间之一"}
    if timeout is not None:
        if not isinstance(timeout, int) or not (5 <= timeout <= 300):
            return 400, {"ok": False, "msg": "超时时间需为 5~300 秒之间的整数"}
    try:
        if key:
            set_api_key(key)
        if base_url:
            set_setting("base_url", base_url.rstrip("/"))
        if model:
            set_setting("model", model)
        if timeout is not None:
            set_setting("timeout", str(timeout))
        # 配置变更：立即清空云端连通性探测缓存，后台刷新拿到最新状态
        _CLOUD_PROBE_CACHE["ts"] = 0.0
        add_log("config-ok", "配置已更新（模型: %s）" % get_cloud_model())
        return 200, {"ok": True,
                     "msg": "配置已保存（当前模型: %s）" % get_cloud_model()}
    except Exception as e:
        return 500, {"ok": False, "msg": "保存失败: %s" % e}


# ============ 启动入口（纯 GUI 桌面应用：直接启动悬浮窗） ============
def ensure_ready():
    """初始化数据库并尝试导入默认种子密钥（GUI 启动前调用）。"""
    _init_db()
    # ★ v1.6.8 升级平滑：新库为空且存在旧版本库时，一次性迁移历史配置，
    #   避免「换了数据目录后开机像丢了配置」。在种子导入前执行，迁移到的
    #   密钥/base_url/model 优先于种子默认值。
    if _migrate_from_legacy_db():
        print("[aiwindow] 已从旧数据库迁移历史配置")
    if ensure_default_key_from_seed():
        print("[aiwindow] 已自动导入默认云端密钥（AES 加密存储）")


def main():
    ensure_ready()
    # 无图形会话（systemd 无头环境）时仅完成数据初始化后退出；
    # 图形桌面登录后由 autostart 启动悬浮窗 GUI（backend.py 即 GUI 启动入口）。
    if not os.environ.get("DISPLAY"):
        print("[aiwindow] 未检测到图形会话（DISPLAY 为空），仅完成数据初始化后退出；"
              "桌面登录后由自启项启动悬浮窗 GUI。")
        return
    print("[aiwindow] 工作模式：纯 GUI 桌面应用 · 纯云端 · 智谱 GLM-4.7-Flash（%s）"
          % DEFAULT_BASE_URL)
    # 直接启动 GTK 悬浮窗主循环（无 HTTP 服务、无端口监听）
    import floating_window as fw
    fw.main()


if __name__ == "__main__":
    main()
