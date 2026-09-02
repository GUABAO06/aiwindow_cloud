#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_v168.py — v1.6.8 回归测试
=========================================================
覆盖本次两大改动：
  1) 配置持久化：未注入环境变量时数据目录锁定 ~/.config/aiwindow（稳定单一），
     环境变量显式设置时优先且不降级；旧库（~/.local/share、/var/lib）一次性迁移。
  2) 模型设置通用化：thinking 私有字段仅智谱下发；探测 /models 404/405 降级
     /chat/completions；预置多平台模板与 base_url 反查。
另含 crypto AES 往返 + 种子密钥自动导入的端到端验证（需系统 openssl）。

用法：
  set HOME=<临时目录>   （测试内部会自建隔离 HOME，无需手动设）
  python test_v168.py
以隔离的临时 HOME 运行，绝不污染真实用户配置。
"""
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _fresh_backend(home, data_env=None, etc_env=None):
    """在隔离 HOME（及可选 AIWINDOW_DATA/ETC）下重新 import backend，
    确保模块级 DATA_DIR/ETC_DIR/DB_PATH 按当前环境重新解析。"""
    for var in ("AIWINDOW_DATA", "AIWINDOW_ETC"):
        os.environ.pop(var, None)
    if data_env:
        os.environ["AIWINDOW_DATA"] = data_env
    if etc_env:
        os.environ["AIWINDOW_ETC"] = etc_env
    # Windows 用 USERPROFILE，POSIX 用 HOME；两者都设保证 expanduser("~") 命中
    os.environ["HOME"] = home
    os.environ["USERPROFILE"] = home
    os.environ.pop("XDG_DATA_HOME", None)
    os.environ.pop("XDG_CONFIG_HOME", None)
    if "backend" in sys.modules:
        del sys.modules["backend"]
    import backend
    return importlib.reload(backend)


class PathPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aiw_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_env_locks_to_home_config(self):
        """未注入环境变量 → 数据目录锁定 ~/.config/aiwindow（不再漂移）。"""
        be = _fresh_backend(self.tmp)
        expected = os.path.join(self.tmp, ".config", "aiwindow")
        self.assertEqual(os.path.abspath(be.DATA_DIR), os.path.abspath(expected))
        self.assertEqual(os.path.abspath(be.ETC_DIR), os.path.abspath(expected))
        self.assertTrue(be.DB_PATH.endswith(os.path.join("aiwindow", "aiwindow.db")))

    def test_env_takes_priority_no_downgrade(self):
        """显式 AIWINDOW_DATA/ETC → 优先且不降级（systemd 场景行为不变）。"""
        data = os.path.join(self.tmp, "var", "lib", "aiwindow")
        etc = os.path.join(self.tmp, "etc", "aiwindow")
        os.makedirs(data, exist_ok=True)
        os.makedirs(etc, exist_ok=True)
        be = _fresh_backend(self.tmp, data_env=data, etc_env=etc)
        self.assertEqual(os.path.abspath(be.DATA_DIR), os.path.abspath(data))
        self.assertEqual(os.path.abspath(be.ETC_DIR), os.path.abspath(etc))

    def test_persist_roundtrip_same_dir_across_restart(self):
        """核心：模拟两次开机（两次 import），同一 HOME 下读到同一份配置。"""
        be = _fresh_backend(self.tmp)
        be._init_db()
        be.set_setting("base_url", "https://api.openai.com/v1")
        be.set_setting("model", "gpt-4o-mini")
        db1 = be.DB_PATH
        # 第二次“开机”：重新解析路径 + 读取
        be2 = _fresh_backend(self.tmp)
        self.assertEqual(be2.DB_PATH, db1)
        self.assertEqual(be2.get_cloud_base_url(), "https://api.openai.com/v1")
        self.assertEqual(be2.get_cloud_model(), "gpt-4o-mini")

    def test_migrate_from_legacy_local_share(self):
        """旧库（~/.local/share/aiwindow）存在配置、新库为空 → 首启自动迁移。"""
        # 先在旧降级路径手工造一份旧库
        legacy_dir = os.path.join(self.tmp, ".local", "share", "aiwindow")
        os.makedirs(legacy_dir, exist_ok=True)
        import sqlite3
        legacy_db = os.path.join(legacy_dir, "aiwindow.db")
        con = sqlite3.connect(legacy_db)
        con.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute("INSERT INTO settings VALUES('base_url','https://api.deepseek.com/v1')")
        con.execute("INSERT INTO settings VALUES('model','deepseek-chat')")
        con.commit()
        con.close()
        # 新库（~/.config/aiwindow）为空，ensure_ready 触发迁移
        be = _fresh_backend(self.tmp)
        be._init_db()
        migrated = be._migrate_from_legacy_db()
        self.assertTrue(migrated, "应从旧库迁移出配置")
        self.assertEqual(be.get_cloud_base_url(), "https://api.deepseek.com/v1")
        self.assertEqual(be.get_cloud_model(), "deepseek-chat")

    def test_migrate_never_overwrites_existing(self):
        """新库已有配置 → 不触发迁移，绝不覆盖用户当前配置。"""
        legacy_dir = os.path.join(self.tmp, ".local", "share", "aiwindow")
        os.makedirs(legacy_dir, exist_ok=True)
        import sqlite3
        con = sqlite3.connect(os.path.join(legacy_dir, "aiwindow.db"))
        con.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute("INSERT INTO settings VALUES('model','old-model')")
        con.commit()
        con.close()
        be = _fresh_backend(self.tmp)
        be._init_db()
        be.set_setting("model", "current-model")   # 新库已有配置
        self.assertFalse(be._migrate_from_legacy_db())
        self.assertEqual(be.get_cloud_model(), "current-model")


class GenericModelConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aiw_test_")
        self.be = _fresh_backend(self.tmp)
        self.be._init_db()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _capture_outgoing(self, base_url):
        """把 base_url 设为目标平台，mock get_api_key 与转发，捕获实际请求体。"""
        self.be.set_setting("base_url", base_url)
        captured = {}

        def fake_forward(payload, burl, api_key=None):
            captured["payload"] = payload
            captured["base_url"] = burl
            return 200, {"choices": [{"message": {"content": "ok"}}]}

        with mock.patch.object(self.be, "get_api_key", return_value="k-test"), \
             mock.patch.object(self.be, "_forward_chat_completions", side_effect=fake_forward):
            self.be.handle_chat({"messages": [{"role": "user", "content": "hi"}]})
        return captured["payload"]

    def test_zhipu_sends_thinking(self):
        """智谱平台：默认下发 thinking.type=enabled。"""
        payload = self._capture_outgoing("https://open.bigmodel.cn/api/paas/v4")
        self.assertIn("thinking", payload)
        self.assertEqual(payload["thinking"], {"type": "enabled"})

    def test_openai_no_thinking(self):
        """OpenAI 平台：不下发 thinking 私有字段（否则 400）。"""
        payload = self._capture_outgoing("https://api.openai.com/v1")
        self.assertNotIn("thinking", payload)
        # 标准字段仍在
        for f in ("model", "messages", "temperature", "max_tokens", "stream"):
            self.assertIn(f, payload)

    def test_deepseek_no_thinking(self):
        payload = self._capture_outgoing("https://api.deepseek.com/v1")
        self.assertNotIn("thinking", payload)

    def test_explicit_thinking_passthrough(self):
        """请求体显式给 thinking → 无条件透传（即便非智谱）。"""
        self.be.set_setting("base_url", "https://api.openai.com/v1")
        captured = {}

        def fake_forward(payload, burl, api_key=None):
            captured["payload"] = payload
            return 200, {"choices": [{"message": {"content": "ok"}}]}

        with mock.patch.object(self.be, "get_api_key", return_value="k"), \
             mock.patch.object(self.be, "_forward_chat_completions", side_effect=fake_forward):
            self.be.handle_chat({
                "messages": [{"role": "user", "content": "hi"}],
                "thinking": {"type": "custom"},
            })
        self.assertEqual(captured["payload"]["thinking"], {"type": "custom"})

    def test_presets_shape(self):
        """预置多平台模板结构正确，含智谱/OpenAI/自定义。"""
        pids = [p[3] for p in self.be.CLOUD_PRESETS]
        for expect in ("zhipu", "openai", "deepseek", "custom"):
            self.assertIn(expect, pids)
        for name, url, model, pid in self.be.CLOUD_PRESETS:
            self.assertTrue(name)
            if pid != "custom":
                self.assertTrue(url.startswith("http"))
                self.assertTrue(model)

    def test_is_zhipu(self):
        self.assertTrue(self.be._is_zhipu("https://open.bigmodel.cn/api/paas/v4"))
        self.assertFalse(self.be._is_zhipu("https://api.openai.com/v1"))
        self.assertFalse(self.be._is_zhipu(""))

    def test_default_model_is_valid_zhipu(self):
        """回归：默认模型为智谱官方免费模型 glm-4.7-flash；智谱预置模板的默认
        model 应与全局默认保持一致。"""
        self.assertEqual(self.be.DEFAULT_MODEL, "glm-4.7-flash")
        # 智谱预置模板的默认 model 应与全局默认一致
        zhipu = [p for p in self.be.CLOUD_PRESETS if p[3] == "zhipu"][0]
        self.assertEqual(zhipu[2], self.be.DEFAULT_MODEL)

    def test_api_key_is_stripped_on_save_and_read(self):
        """回归 403/401 根因：从网页/文档复制的密钥常带尾部空格或换行，
        存取时必须 strip，否则会污染 Authorization 头导致鉴权异常。"""
        self.be.set_api_key("  sk-clean-KEY-123  \n")
        self.assertEqual(self.be.get_api_key(), "sk-clean-KEY-123")

    def test_extract_server_msg(self):
        """能从平台 {"error":{"message":...}} 响应体提取原始错误信息。"""
        self.assertEqual(
            self.be._extract_server_msg({"error": {"message": "您无权访问该模型"}}),
            "您无权访问该模型")
        self.assertEqual(self.be._extract_server_msg({}), "")

    def test_403_error_message_mentions_permission(self):
        """403 文案应指向「无权访问模型」而非笼统「密钥无效」，并带平台原始提示。"""
        msg = self.be._friendly_cloud_error(403, "您无权访问 glm-x")
        self.assertIn("无权", msg)
        self.assertIn("您无权访问 glm-x", msg)


class ProbeFallbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aiw_test_")
        self.be = _fresh_backend(self.tmp)
        self.be._init_db()
        self.be.set_setting("base_url", "https://gateway.example.com/v1")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_models_404_falls_back_to_chat(self):
        """/models 返回 404 → 降级用 /chat/completions 探测，200 视为已连接。"""
        import urllib.error

        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            if url.endswith("/models"):
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            # /chat/completions 探测：返回 200
            class R:
                status = 200
                def read(self): return b'{"choices":[]}'
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return R()

        with mock.patch.object(self.be, "get_api_key", return_value="k"), \
             mock.patch.object(self.be, "has_api_key", return_value=True), \
             mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok, detail, status = self.be._cloud_probe()
        self.assertTrue(ok, "404 /models 后应降级 chat 探测成功。detail=%s" % detail)
        self.assertEqual(status, 200)

    def test_models_200_direct_ok(self):
        """/models 直接 200 → 无需降级即已连接。"""
        def fake_urlopen(req, timeout=None):
            class R:
                status = 200
                def read(self): return b'{"data":[]}'
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return R()

        with mock.patch.object(self.be, "get_api_key", return_value="k"), \
             mock.patch.object(self.be, "has_api_key", return_value=True), \
             mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok, detail, status = self.be._cloud_probe()
        self.assertTrue(ok)
        self.assertEqual(status, 200)

    def test_auth_fail_not_masked(self):
        """/models 返回 401 → 直接报鉴权失败，不降级（避免掩盖真实错误）。"""
        import urllib.error

        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

        with mock.patch.object(self.be, "get_api_key", return_value="bad"), \
             mock.patch.object(self.be, "has_api_key", return_value=True), \
             mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok, detail, status = self.be._cloud_probe()
        self.assertFalse(ok)
        self.assertEqual(status, 401)


class CryptoSeedTest(unittest.TestCase):
    """crypto AES 往返 + 种子密钥自动导入端到端（需系统 openssl）。"""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aiw_test_")
        self.be = _fresh_backend(self.tmp)
        import crypto
        self.crypto = importlib.reload(crypto)
        if shutil.which("openssl") is None:
            self.skipTest("系统无 openssl，跳过 crypto 端到端测试")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_aes_roundtrip(self):
        pw = self.crypto.gen_password(32)
        enc = self.crypto.aes_encrypt_text("secret-key-123", pw)
        self.assertEqual(self.crypto.aes_decrypt_text(enc, pw), "secret-key-123")

    def test_decrypt_tolerates_stripped_and_crlf_base64(self):
        """跨平台回归：openssl 输出的 base64 在存储后可能被 strip 掉尾部换行，或
        因 Windows 换行成为 CRLF。解密端须对这两种形态均能成功还原，
        避免历史上出现的 openssl "error reading input file"。"""
        pw = self.crypto.gen_password(32)
        enc = self.crypto.aes_encrypt_text("sk-abc-XYZ", pw)  # 单行 base64
        # 1) 被 strip 掉尾部换行（种子文件常见）
        self.assertEqual(self.crypto.aes_decrypt_text(enc.strip(), pw), "sk-abc-XYZ")
        # 2) 显式补 CRLF 结尾
        self.assertEqual(self.crypto.aes_decrypt_text(enc.strip() + "\r\n", pw), "sk-abc-XYZ")
        # 3) 显式补 LF 结尾
        self.assertEqual(self.crypto.aes_decrypt_text(enc.strip() + "\n", pw), "sk-abc-XYZ")

    def test_encrypt_outputs_single_line(self):
        """加密输出应为无换行单行 base64（-A），存储/传输不受换行差异影响。"""
        pw = self.crypto.gen_password(32)
        enc = self.crypto.aes_encrypt_text("payload-长文本" * 8, pw)
        self.assertNotIn("\n", enc)
        self.assertNotIn("\r", enc)
        self.assertEqual(self.crypto.aes_decrypt_text(enc, pw), "payload-长文本" * 8)

    def test_seed_import_with_paired_bootstrap(self):
        """配对的 seed_key.enc + bootstrap.key 放在 ETC_DIR → 首启自动导入成功。"""
        etc = self.be.ETC_DIR
        os.makedirs(etc, exist_ok=True)
        pw = self.crypto.gen_password(32)
        with open(os.path.join(etc, "bootstrap.key"), "w", encoding="utf-8") as f:
            f.write(pw)
        enc = self.crypto.aes_encrypt_text("sk-my-real-key", pw)
        with open(os.path.join(etc, "seed_key.enc"), "w", encoding="utf-8") as f:
            f.write(enc)
        # 重新解析（此时 SEED_FILE/BOOTSTRAP_FILE 指向刚写入的 ETC_DIR 文件）
        be = _fresh_backend(self.tmp)
        be._init_db()
        self.assertTrue(be.ensure_default_key_from_seed(), "配对种子应导入成功")
        self.assertEqual(be.get_api_key(), "sk-my-real-key")
        # 默认 base_url/model 一并预置
        self.assertEqual(be.get_cloud_base_url(), be.DEFAULT_BASE_URL)


if __name__ == "__main__":
    # 让 crypto 能找到 Anaconda 自带 openssl（Windows 构建机）
    for cand in (r"B:\Anaconda3\Library\bin", ):
        if os.path.isdir(cand) and cand not in os.environ.get("PATH", ""):
            os.environ["PATH"] = cand + os.pathsep + os.environ.get("PATH", "")
    unittest.main(verbosity=2)
