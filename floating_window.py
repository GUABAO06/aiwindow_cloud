#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# floating_window.py — aiwindow 悬浮窗 GUI（v1.6.7 · GTK3 单窗口双形态）
# ★ v1.6.7 新增：设置页「模型连接状态」行（绿=已连接 HTTP200 / 红=鉴权失败或
#   网络异常 / 灰=未配置密钥），打开设置页自动探测、保存成功后与「刷新」按钮
#   均即时刷新。
# ★ v1.6.6 修复：__init__ 补初始化 _suppress_mode_combo / _last_mode_idx，
#   修复模式下拉「设置…」进不去设置页的 AttributeError。
# ★ 悬浮球右键不再弹出菜单：右键单击小圆球不再触发任何菜单，
#   退出入口保留在托盘菜单与设置页「退出应用」按钮（弹确认框）。
# ★ 顶层窗口不在任务栏显示：set_skip_taskbar_hint + set_skip_pager_hint +
#   set_type_hint(UTILITY)，并在窗口映射后延迟重设 skip 提示，
#   任务栏/分页器均不显示本窗口按钮（兼容部分窗口管理器延迟生效）。
# ★ 设置入口移入模式下拉列表：展开态头部不再单独显示「设置」按钮，
#   模式下拉末尾追加「设置…」项（分隔线后），选中自动打开设置页并恢复原模式。
# ★ 后台运行：关闭悬浮窗（delete-event / Alt+F4 / WM 关闭请求）仅隐藏窗口、
#   悬浮球后台驻留，不退出进程；托盘图标点击切换显隐、右键菜单可退出；
#   展开态「设置」页新增「退出应用」按钮（弹确认框，确认才真正退出进程）。
# 保留既有能力：双形态（收缩=球 / 展开=球+聊天面板同窗）、三模式（问答/
# 代码解释/文本润色）、划词带入、Enter发送/Shift+Enter换行、拖拽吸附、
# 展开态内嵌「设置」界面（密钥/base_url/model/timeout、版本与智谱信息、日志）。
import os
import sys
import html
import time
import threading
import backend
try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk, GLib
    GTK_OK = True
except Exception:
    Gtk = Gdk = GLib = None
    GTK_OK = False

APP_NAME = "aiwindow 助手"

BALL_SIZE = 56
GAP = 10
PANEL_W = 400
PANEL_H = 520
SLIVER = 14
SNAP_MARGIN = 40
MAX_HISTORY = 8
ANIM_MS = 16

BG_MAIN = "#f7f8fa"
FG_MAIN = "#1f2937"
BLUE = "#3b82f6"
BORDER_GRAY = "#c2c8d4"

FORM_COLLAPSED = "collapsed"
FORM_EXPANDED = "expanded"

MODE_QA = "问答"
MODE_CODE = "代码解释"
MODE_POLISH = "文本润色"
MODES = [MODE_QA, MODE_CODE, MODE_POLISH]
SETTINGS_ENTRY = "设置…"   # 模式下拉末尾的「设置…」假选项（选中即打开设置页）
MODE_SYSTEM = {
    MODE_QA: "你是一个智能助手，请直接、准确地回答用户的问题。",
    MODE_CODE: "你是一个编程专家，请解释用户提供的代码：说明其作用、关键逻辑与注意事项。",
    MODE_POLISH: "你是一个文字编辑专家，请对用户提供的文本进行润色，使其表达更通顺、专业、简洁，不改变原意。",
}

def _html_escape(text):
    return html.escape(str(text))

def build_messages(mode, history):
    sys_prompt = MODE_SYSTEM.get(mode, MODE_SYSTEM[MODE_QA])
    return [{"role": "system", "content": sys_prompt}] + list(history)

def snap_x(side, left, right):
    if side == "left":
        return left
    return right - BALL_SIZE

def collapse_x(side, left, right):
    if side == "left":
        return left - (BALL_SIZE - SLIVER)
    return right - SLIVER

def expanded_size():
    return BALL_SIZE + GAP + PANEL_W, max(BALL_SIZE, PANEL_H)

def call_backend(messages, timeout=120):
    """进程内直调 backend.handle_chat（不再走 HTTP）。"""
    payload = {
        "model": "",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1024,
        "stream": False,
    }
    try:
        status, data = backend.handle_chat(payload)
    except Exception as e:
        return "", "请求失败：%s" % e
    if status == 200:
        try:
            content = data["choices"][0]["message"]["content"]
            return str(content), ""
        except (KeyError, IndexError, TypeError):
            return "", "云端响应格式异常"
    error = (data or {}).get("error") or "服务异常（状态码 %d）" % status
    return "", str(error)

if GTK_OK:
    class FloatingPanel(Gtk.Window):
        def __init__(self, start_form=FORM_COLLAPSED):
            super().__init__(type=Gtk.WindowType.TOPLEVEL)
            self._form = None
            self._press_global = None
            self._window_pos = None
            self._moved = False
            self._dragging = False
            self._snapped = None
            self._collapse_timer = None
            self._anim_source = None
            self._history = []
            self._busy = False
            self._last_seen_clip = None
            self._tray = None
            # ★ v1.6.6 修复：mode_combo 回调前必须先初始化这两个状态变量，
            #   否则选中「设置…」时 _on_mode_combo_changed 抛 AttributeError 进不去设置页。
            self._suppress_mode_combo = False
            self._last_mode_idx = 0
            # ★ v1.6.8：平台下拉回填时抑制其 changed 回调，避免回填 base_url/model
            #   时被模板值覆盖用户已保存的自定义值。
            self._suppress_provider = False

            self.set_decorated(False)
            self.set_keep_above(True)
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
            self.set_resizable(False)
            self.set_app_paintable(True)
            self.get_style_context().add_class("aiw-root")
            self._make_transparent()
            self.connect("map-event", self._on_map)
            self.connect("delete-event", self._on_delete)
            self._build_ui()
            self._setup_tray()
            self._move_to_default()
            self.set_form(start_form, report=False)
            self._report_state()
            # 兼容部分窗口管理器：show 后延迟重设任务栏/分页器跳过提示
            GLib.timeout_add(300, self._reassert_skip_hints)

        def _make_transparent(self):
            screen = self.get_screen()
            visual = screen.get_rgba_visual()
            if visual is not None:
                self.set_visual(visual)

        # ---------- 后台运行：托盘图标 + 关闭即驻留 ----------
        def _setup_tray(self):
            """创建系统托盘图标（后台驻留的恢复/退出入口）。
            点击托盘图标切换悬浮窗显隐；右键菜单可「显示悬浮球 / 退出 aiwindow」。
            托盘不可用时降级：仅保留设置页「退出应用」按钮作为退出入口。"""
            try:
                tray = Gtk.StatusIcon()
                tray.set_from_icon_name("system-run")
                tray.set_tooltip_text("%s（点击显示/隐藏悬浮球）" % APP_NAME)
                tray.connect("activate", lambda t: self._toggle_tray())
                tray.connect("popup-menu", self._on_tray_menu)
                self._tray = tray
            except Exception:
                self._tray = None

        def _toggle_tray(self):
            """点击托盘：悬浮窗隐藏时显示，显示时隐藏（进程始终驻留）。"""
            if self.get_visible():
                self.hide()
            else:
                self.show_all()
                self._report_state()

        def _on_tray_menu(self, status_icon, button, activate_time):
            menu = Gtk.Menu()
            item_show = Gtk.MenuItem(label="显示悬浮球")
            item_show.connect("activate", lambda w: self._show_from_tray())
            menu.append(item_show)
            item_exit = Gtk.MenuItem(label="退出 aiwindow")
            item_exit.connect("activate", lambda w: self._confirm_exit())
            menu.append(item_exit)
            menu.show_all()
            menu.popup(None, None, Gtk.StatusIcon.position_menu,
                       status_icon, button, activate_time)

        def _show_from_tray(self):
            self.show_all()
            self._report_state()

        def _on_delete(self, widget, event):
            """WM 关闭请求（delete-event / Alt+F4 / 窗口管理器关闭）：
            不退出进程，隐藏窗口、悬浮球后台驻留。"""
            self.hide()
            self._report_state()
            return True  # 阻止默认销毁窗口

        def _confirm_exit(self):
            """真正的退出入口：确认后才退出进程（关闭窗口不触发）。"""
            dlg = Gtk.MessageDialog(
                transient_for=self, modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.YES_NO,
                text="确定要退出 aiwindow 吗？")
            dlg.format_secondary_text(
                "关闭悬浮窗仅会隐藏到后台驻留（托盘图标仍可唤回）；\n"
                "确认退出后进程将完全结束，需重新启动才能再次使用。")
            resp = dlg.run()
            dlg.destroy()
            if resp == Gtk.ResponseType.YES:
                self._do_exit()

        def _build_ui(self):
            self.fixed = Gtk.Fixed()
            self.add(self.fixed)

            self.ball = Gtk.EventBox()
            self.ball.set_size_request(BALL_SIZE, BALL_SIZE)
            self.ball.get_style_context().add_class("aiw-ball")
            ball_label = Gtk.Label(label="AI")
            ball_label.get_style_context().add_class("aiw-ball-label")
            self.ball.add(ball_label)
            self.ball.add_events(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.BUTTON_RELEASE_MASK
                | Gdk.EventMask.POINTER_MOTION_MASK
                | Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
            )
            self.ball.connect("button-press-event", self._on_ball_press)
            self.ball.connect("button-release-event", self._on_ball_release)
            self.ball.connect("motion-notify-event", self._on_ball_motion)
            self.ball.connect("enter-notify-event", self._on_ball_enter)
            self.ball.connect("leave-notify-event", self._on_ball_leave)
            self.fixed.put(self.ball, 0, 0)

            self.panel_evt = Gtk.EventBox()
            self.panel_evt.get_style_context().add_class("aiw-panel")
            self.panel_evt.set_size_request(PANEL_W, PANEL_H)
            self.panel_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.panel_box.set_margin_top(10)
            self.panel_box.set_margin_bottom(10)
            self.panel_box.set_margin_start(12)
            self.panel_box.set_margin_end(12)
            self.panel_evt.add(self.panel_box)
            self.fixed.put(self.panel_evt, BALL_SIZE + GAP, 0)

            self._build_panel_ui()
            self._apply_css()

        def _build_panel_ui(self):
            box = self.panel_box

            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            btn_fold = Gtk.Button(label="收起")
            btn_fold.set_size_request(52, 24)
            btn_fold.get_style_context().add_class("aiw-btn-fold")
            btn_fold.connect("clicked", lambda b: self._toggle_form())
            head.pack_start(btn_fold, False, False, 0)
            title = Gtk.Label(label=APP_NAME)
            title.set_halign(Gtk.Align.START)
            title.get_style_context().add_class("aiw-title")
            head.pack_start(title, False, False, 0)
            box.pack_start(head, False, False, 0)

            self.stack = Gtk.Stack()
            self.stack.set_transition_type(Gtk.StackTransitionType.NONE)

            chat_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

            mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            lbl_mode = Gtk.Label(label="模式")
            lbl_mode.get_style_context().add_class("aiw-muted")
            mode_row.pack_start(lbl_mode, False, False, 0)
            self.mode_combo = Gtk.ComboBoxText()
            for m in MODES:
                self.mode_combo.append_text(m)
            # GTK3 的 Gtk.ComboBoxText 无 append_separator()，直接追加「设置…」假选项
            self.mode_combo.append_text(SETTINGS_ENTRY)
            self.mode_combo.set_active(0)
            self.mode_combo.connect("changed", self._on_mode_combo_changed)
            mode_row.pack_start(self.mode_combo, False, False, 0)
            self.lbl_clip = Gtk.Label(label="")
            self.lbl_clip.set_halign(Gtk.Align.END)
            self.lbl_clip.get_style_context().add_class("aiw-clip")
            mode_row.pack_end(self.lbl_clip, True, True, 0)
            chat_page.pack_start(mode_row, False, False, 0)

            self.view = Gtk.TextView()
            self.view.set_editable(False)
            self.view.set_cursor_visible(False)
            self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            self.view.get_style_context().add_class("aiw-view")
            self._buf = self.view.get_buffer()
            self._buf.set_text("你好，我是 aiwindow 助手，有什么可以帮你？")
            sw = Gtk.ScrolledWindow()
            sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            sw.add(self.view)
            chat_page.pack_start(sw, True, True, 0)

            input_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            input_row.set_size_request(-1, 100)

            input_overlay = Gtk.Overlay()
            self.input_edit = Gtk.TextView()
            self.input_edit.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            self.input_edit.set_can_focus(True)
            self.input_edit.get_style_context().add_class("aiw-input")
            self._input_buf = self.input_edit.get_buffer()
            self._input_buf.set_text("")
            self.input_edit.connect("key-press-event", self._on_input_key)
            input_overlay.add(self.input_edit)

            self.placeholder_label = Gtk.Label(label="输入问题，Enter 发送 / Shift+Enter 换行")
            self.placeholder_label.get_style_context().add_class("aiw-placeholder")
            self.placeholder_label.set_halign(Gtk.Align.START)
            self.placeholder_label.set_valign(Gtk.Align.START)
            self.placeholder_label.set_margin_start(12)
            self.placeholder_label.set_margin_top(12)
            input_overlay.add_overlay(self.placeholder_label)

            self._input_buf.connect("changed", self._on_input_buf_changed)
            input_row.pack_start(input_overlay, True, True, 0)

            self.btn_send = Gtk.Button(label="发送")
            self.btn_send.set_size_request(64, 64)
            self.btn_send.get_style_context().add_class("aiw-send")
            self.btn_send.connect("clicked", lambda b: self._send())
            input_row.pack_end(self.btn_send, False, False, 0)
            chat_page.pack_start(input_row, False, False, 4)

            self.stack.add_named(chat_page, "chat")
            self.stack.add_named(self._build_settings_ui(), "settings")
            self.stack.set_visible_child_name("chat")
            box.pack_start(self.stack, True, True, 0)

        def _build_settings_ui(self):
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            btn_back = Gtk.Button(label="返回")
            btn_back.set_size_request(52, 24)
            btn_back.get_style_context().add_class("aiw-btn-fold")
            btn_back.connect("clicked", lambda b: self._show_chat())
            head.pack_start(btn_back, False, False, 0)
            stitle = Gtk.Label(label="设置")
            stitle.set_halign(Gtk.Align.START)
            stitle.get_style_context().add_class("aiw-title")
            head.pack_start(stitle, False, False, 0)
            page.pack_start(head, False, False, 0)

            self.lbl_provider = Gtk.Label(label="")
            self.lbl_provider.set_halign(Gtk.Align.START)
            self.lbl_provider.get_style_context().add_class("aiw-muted")
            page.pack_start(self.lbl_provider, False, False, 0)

            grid = Gtk.Grid(column_spacing=8, row_spacing=6)
            grid.set_halign(Gtk.Align.FILL)

            def add_row(r, name, widget):
                lbl = Gtk.Label(label=name)
                lbl.set_halign(Gtk.Align.START)
                lbl.get_style_context().add_class("aiw-muted")
                grid.attach(lbl, 0, r, 1, 1)
                grid.attach(widget, 1, r, 1, 1)

            # ★ v1.6.8 通用模型配置：平台快捷模板下拉。选中自动填充 base_url
            #   与常见 model，减少重复手填；「自定义」不覆盖，供手动填写任意平台。
            self.combo_provider = Gtk.ComboBoxText()
            for name, _url, _model, _pid in backend.CLOUD_PRESETS:
                self.combo_provider.append_text(name)
            self.combo_provider.set_active(0)
            self.combo_provider.connect("changed", self._on_provider_changed)
            add_row(0, "平台", self.combo_provider)

            self.entry_key = Gtk.Entry()
            self.entry_key.set_visibility(False)
            self.entry_key.set_placeholder_text("云端 API Key（对应所选平台）")
            self.entry_key.get_style_context().add_class("aiw-input")
            add_row(1, "API Key", self.entry_key)

            self.chk_show_key = Gtk.CheckButton(label="显示密钥")
            self.chk_show_key.connect("toggled", lambda c: self.entry_key.set_visibility(c.get_active()))
            key_extra = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            key_extra.pack_start(self.chk_show_key, False, False, 0)
            grid.attach(key_extra, 1, 2, 1, 1)

            self.entry_base = Gtk.Entry()
            self.entry_base.set_text(backend.DEFAULT_BASE_URL)
            self.entry_base.set_placeholder_text("OpenAI 兼容服务地址，如 https://api.openai.com/v1")
            self.entry_base.get_style_context().add_class("aiw-input")
            add_row(3, "base_url", self.entry_base)

            self.entry_model = Gtk.Entry()
            self.entry_model.set_text(backend.DEFAULT_MODEL)
            self.entry_model.set_placeholder_text("模型名，如 gpt-4o-mini / glm-4.7-flash")
            self.entry_model.get_style_context().add_class("aiw-input")
            add_row(4, "model", self.entry_model)

            self.entry_timeout = Gtk.Entry()
            self.entry_timeout.set_text(str(backend.DEFAULT_TIMEOUT))
            self.entry_timeout.get_style_context().add_class("aiw-input")
            add_row(5, "timeout(秒)", self.entry_timeout)

            page.pack_start(grid, False, False, 0)

            # ★ v1.6.7 当前模型连接状态：绿=已连接 / 红=鉴权失败或网络异常 / 灰=未配置密钥
            status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl_status_name = Gtk.Label(label="模型连接状态")
            lbl_status_name.set_halign(Gtk.Align.START)
            lbl_status_name.get_style_context().add_class("aiw-muted")
            status_row.pack_start(lbl_status_name, False, False, 0)
            self.lbl_status = Gtk.Label(label="检测中…")
            self.lbl_status.set_halign(Gtk.Align.START)
            self.lbl_status.get_style_context().add_class("aiw-status-gray")
            status_row.pack_start(self.lbl_status, False, False, 0)
            page.pack_start(status_row, False, False, 0)

            save_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.btn_save = Gtk.Button(label="保存")
            self.btn_save.set_size_request(64, 30)
            self.btn_save.get_style_context().add_class("aiw-send")
            self.btn_save.connect("clicked", lambda b: self._save_settings())
            save_row.pack_start(self.btn_save, False, False, 0)
            self.lbl_save = Gtk.Label(label="")
            self.lbl_save.set_halign(Gtk.Align.START)
            self.lbl_save.get_style_context().add_class("aiw-clip")
            save_row.pack_start(self.lbl_save, False, False, 0)
            page.pack_start(save_row, False, False, 0)

            exit_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            btn_exit = Gtk.Button(label="退出应用")
            btn_exit.set_size_request(80, 30)
            btn_exit.get_style_context().add_class("aiw-btn-fold")
            btn_exit.connect("clicked", lambda b: self._confirm_exit())
            exit_row.pack_start(btn_exit, False, False, 0)
            lbl_exit = Gtk.Label(label="关闭悬浮窗仅隐藏到后台驻留，此按钮才真正退出进程")
            lbl_exit.set_halign(Gtk.Align.START)
            lbl_exit.get_style_context().add_class("aiw-muted")
            exit_row.pack_start(lbl_exit, False, False, 0)
            page.pack_start(exit_row, False, False, 0)

            logs_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            logs_title = Gtk.Label(label="调用日志")
            logs_title.get_style_context().add_class("aiw-muted")
            logs_head.pack_start(logs_title, False, False, 0)
            btn_refresh = Gtk.Button(label="刷新")
            btn_refresh.set_size_request(52, 24)
            btn_refresh.get_style_context().add_class("aiw-btn-fold")
            btn_refresh.connect(
                "clicked", lambda b: (self._refresh_logs(), self._refresh_cloud_status()))
            logs_head.pack_end(btn_refresh, False, False, 0)
            page.pack_start(logs_head, False, False, 0)

            self.logs_view = Gtk.TextView()
            self.logs_view.set_editable(False)
            self.logs_view.set_cursor_visible(False)
            self.logs_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            self.logs_view.get_style_context().add_class("aiw-view")
            self._logs_buf = self.logs_view.get_buffer()
            self._logs_buf.set_text("（暂无日志）")
            logs_sw = Gtk.ScrolledWindow()
            logs_sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            logs_sw.set_size_request(-1, 150)
            logs_sw.add(self.logs_view)
            page.pack_start(logs_sw, True, True, 0)

            return page

        def _show_settings(self):
            """从 SQLite 读取当前配置并切换到设置页。"""
            try:
                self.entry_key.set_text(backend.get_api_key() or "")
            except Exception:
                self.entry_key.set_text("")
            cur_base = backend.get_cloud_base_url() or backend.DEFAULT_BASE_URL
            self.entry_base.set_text(cur_base)
            self.entry_model.set_text(backend.get_cloud_model() or backend.DEFAULT_MODEL)
            self.entry_timeout.set_text(str(backend.get_timeout()))
            # ★ v1.6.8 平台下拉反查：根据当前 base_url 匹配预置模板并选中，
            #   匹配不到则落到「自定义」。抑制 changed 回调避免覆盖已存配置。
            self._suppress_provider = True
            self.combo_provider.set_active(self._provider_index_for_base_url(cur_base))
            self._suppress_provider = False
            # ★ v1.6.8 文案通用化：不再硬编码「智谱」，展示当前平台名 + 版本。
            self.lbl_provider.set_text(
                "服务商：%s · 应用版本 v%s"
                % (self._provider_name_for_base_url(cur_base), backend.APP_VERSION))
            self.lbl_save.set_text("")
            self._refresh_logs()
            self._refresh_cloud_status()
            self.stack.set_visible_child_name("settings")

        def _provider_index_for_base_url(self, base_url):
            """按 base_url 精确匹配预置平台下拉索引，匹配不到返回「自定义」索引。"""
            norm = (base_url or "").rstrip("/")
            for i, (_name, url, _model, _pid) in enumerate(backend.CLOUD_PRESETS):
                if url and url.rstrip("/") == norm:
                    return i
            for i, (_name, _url, _model, pid) in enumerate(backend.CLOUD_PRESETS):
                if pid == "custom":
                    return i
            return 0

        def _provider_name_for_base_url(self, base_url):
            idx = self._provider_index_for_base_url(base_url)
            return backend.CLOUD_PRESETS[idx][0]

        def _on_provider_changed(self, combo):
            """★ v1.6.8 平台下拉切换：自动填充该平台的 base_url 与常见 model。
            「自定义」不改动现有输入（供手动填写任意 OpenAI 兼容平台）。
            回填过程（_suppress_provider）触发的 changed 直接忽略。"""
            if self._suppress_provider:
                return
            idx = combo.get_active()
            if idx is None or idx < 0 or idx >= len(backend.CLOUD_PRESETS):
                return
            _name, url, model, pid = backend.CLOUD_PRESETS[idx]
            if pid == "custom":
                return
            self.entry_base.set_text(url)
            self.entry_model.set_text(model)

        def _show_chat(self):
            self.stack.set_visible_child_name("chat")
            GLib.timeout_add(120, lambda: (self.input_edit.grab_focus(), False))

        def _on_mode_combo_changed(self, combo):
            """模式下拉变化：选中「设置…」假选项时打开设置页并恢复原模式。"""
            if self._suppress_mode_combo:
                return
            text = combo.get_active_text()
            if text == SETTINGS_ENTRY:
                prev = self._last_mode_idx
                self._suppress_mode_combo = True
                combo.set_active(prev)
                self._suppress_mode_combo = False
                self._show_settings()
            else:
                idx = combo.get_active()
                if idx is not None and idx >= 0:
                    self._last_mode_idx = idx

        def _save_settings(self):
            base_url = self.entry_base.get_text().strip()
            model = self.entry_model.get_text().strip()
            timeout_text = self.entry_timeout.get_text().strip()
            key = self.entry_key.get_text().strip()

            if not base_url.startswith("http://") and not base_url.startswith("https://"):
                self.lbl_save.set_text("base_url 需以 http(s):// 开头")
                return
            if not model:
                self.lbl_save.set_text("model 不能为空")
                return
            try:
                timeout = int(timeout_text)
            except ValueError:
                self.lbl_save.set_text("timeout 需为整数秒")
                return
            timeout = max(5, min(300, timeout))

            try:
                backend.set_setting("base_url", base_url.rstrip("/"))
                backend.set_setting("model", model)
                backend.set_setting("timeout", str(timeout))
                if key:
                    backend.set_api_key(key)
                backend.add_log("config-save", "已保存设置：model=%s" % model)
                self.lbl_save.set_text("已保存")
                self._refresh_cloud_status()
            except Exception as e:
                self.lbl_save.set_text("保存失败：%s" % e)
                backend.add_log("config-fail", "保存设置失败: %s" % e)

        def _refresh_logs(self):
            try:
                logs = backend.list_logs(100)
            except Exception:
                logs = []
            if not logs:
                self._logs_buf.set_text("（暂无日志）")
            else:
                text = "\n".join(
                    "[%s] %s %s" % (row.get("ts", ""), row.get("status", ""), row.get("summary", ""))
                    for row in logs
                )
                self._logs_buf.set_text(text)
            adj = self.logs_view.get_vadjustment()
            if adj is not None:
                adj.set_value(adj.get_upper() - adj.get_page_size())

        def _refresh_cloud_status(self):
            """★ v1.6.7 后台线程探测云端连接状态，避免阻塞 GUI，完成后回主线程着色。"""
            self.lbl_status.set_text("检测中…")
            self.lbl_status.get_style_context().remove_class("aiw-status-ok")
            self.lbl_status.get_style_context().remove_class("aiw-status-bad")
            self.lbl_status.get_style_context().add_class("aiw-status-gray")

            def worker():
                try:
                    status_text, level = backend.get_cloud_status()
                except Exception as e:
                    status_text, level = "状态探测失败：%s" % e, "bad"
                GLib.idle_add(self._apply_cloud_status, status_text, level)

            threading.Thread(target=worker, daemon=True).start()

        def _apply_cloud_status(self, status_text, level):
            ctx = self.lbl_status.get_style_context()
            ctx.remove_class("aiw-status-ok")
            ctx.remove_class("aiw-status-bad")
            ctx.remove_class("aiw-status-gray")
            ctx.add_class("aiw-status-%s" % (level if level in ("ok", "bad") else "gray"))
            self.lbl_status.set_text(status_text)

        def _on_input_buf_changed(self, buf):
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            if len(text.strip()) == 0:
                self.placeholder_label.show()
            else:
                self.placeholder_label.hide()

        def _apply_css(self):
            provider = Gtk.CssProvider()
            css_str = """
            .aiw-root { background: transparent; }
            .aiw-panel { background: #ffffff; border-radius:12px; border:1px solid #dde1e8; }
            .aiw-title { color:#1f2937; font-size:14px; font-weight:bold; }
            .aiw-muted { color:#6b7280; font-size:12px; }
            .aiw-clip { color:#16a34a; font-size:12px; }
            /* ★ v1.6.7 模型连接状态着色 */
            .aiw-status-ok   { color:#16a34a; font-size:12px; font-weight:bold; }
            .aiw-status-bad  { color:#dc2626; font-size:12px; font-weight:bold; }
            .aiw-status-gray { color:#9ca3af; font-size:12px; }
            .aiw-ball { background:#3b82f6; border-radius:28px; border:none; }
            .aiw-ball-label { color:#ffffff; font-size:14px; font-weight:bold; }
            .aiw-btn-fold { background:#eef1f6; color:#1f2937; border:none; border-radius:6px; font-size:12px; }

            .aiw-view {
                background:#f2f4f7;
                color:#1f2937;
                font-size:13px;
                border-radius:8px;
                padding:10px;
                border: 0px;
            }

            .aiw-input {
                background:#ffffff;
                color:#111111;
                font-size:13px;
                border-radius:8px;
                padding:10px;
                border:2px solid #c2c8d4;
            }
            .aiw-input:focus {
                border-color:#3b82f6;
            }
            .aiw-placeholder {
                color:#9ca3af;
                font-size:13px;
            }
            .aiw-send {
                background:#3b82f6;
                color:#ffffff;
                font-size:14px;
                border:none;
                border-radius:8px;
            }
            .aiw-send:hover { background:#2f6fd8; }
            .aiw-send:disabled { background:#b8c4d6; }
            """
            provider.load_from_data(css_str.encode("utf-8"))
            Gtk.StyleContext.add_provider_for_screen(
                self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        def _toggle_form(self):
            nxt = FORM_COLLAPSED if self._form == FORM_EXPANDED else FORM_EXPANDED
            self.set_form(nxt, report=True)

        def set_form(self, form, report=True):
            if form not in (FORM_COLLAPSED, FORM_EXPANDED):
                form = FORM_COLLAPSED
            if form == self._form:
                return
            self._form = form
            if form == FORM_EXPANDED:
                self._unsnap()
                w, h = expanded_size()
                self.resize(w, h)
                self.panel_evt.show_all()
                self.panel_evt.set_visible(True)
                self._ensure_on_screen()
                GLib.timeout_add(120, lambda: (self.input_edit.grab_focus(), False))
            else:
                self.resize(BALL_SIZE, BALL_SIZE)
                self.panel_evt.hide()
            if report:
                self._report_state()

        def _on_map(self, widget, event):
            if self._form == FORM_COLLAPSED:
                self.panel_evt.hide()
            else:
                self.panel_evt.show_all()
            # 部分窗口管理器在窗口映射后才生效，延迟重设任务栏/分页器跳过提示
            GLib.timeout_add(200, self._reassert_skip_hints)
            return False

        def _reassert_skip_hints(self):
            """窗口映射后重设任务栏/分页器跳过提示（兼容 WM 延迟生效）。"""
            self.set_skip_taskbar_hint(True)
            self.set_skip_pager_hint(True)
            self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
            return False

        def _ensure_on_screen(self):
            try:
                wa = self._monitor_workarea()
                x, y = self.get_position()
                alloc = self.get_allocation()
                w = alloc.width if alloc.width >1 else BALL_SIZE+GAP+PANEL_W
                if x < wa.x: x = wa.x
                if x + w > wa.x + wa.width: x = wa.x + wa.width - w
                if y < wa.y: y = wa.y
                self.move(int(x), int(y))
            except Exception:
                pass

        def _monitor_workarea(self):
            display = Gdk.Display.get_default()
            try:
                mon = display.get_primary_monitor()
            except Exception:
                mon = display.get_default_screen().get_monitor(0)
            try:
                return mon.get_workarea()
            except Exception:
                return mon.get_geometry()

        def _move_to_default(self):
            try:
                wa = self._monitor_workarea()
                self.move(wa.x + wa.width - BALL_SIZE -40, wa.y + wa.height - BALL_SIZE -60)
            except Exception:
                pass

        def _unsnap(self):
            if self._snapped:
                side = self._snapped
                self._snapped = None
                wa = self._monitor_workarea()
                self._animate_to(snap_x(side, wa.x, wa.x+wa.width), self.get_position()[1],150)

        def _snap(self, side):
            wa = self._monitor_workarea()
            self._snapped = side
            self._animate_to(collapse_x(side, wa.x, wa.x+wa.width), self.get_position()[1], 220)

        def _expand_snap(self):
            if not self._snapped:
                return
            side = self._snapped
            wa = self._monitor_workarea()
            self._animate_to(snap_x(side, wa.x, wa.x+wa.width), self.get_position()[1],180)

        def _cancel_pending_collapse(self):
            if self._collapse_timer is not None:
                GLib.source_remove(self._collapse_timer)
                self._collapse_timer = None

        def _schedule_collapse(self):
            self._cancel_pending_collapse()
            self._collapse_timer = GLib.timeout_add(150, self._do_collapse)

        def _do_collapse(self):
            self._collapse_timer = None
            if self._snapped and not self._dragging:
                if not self._pointer_over_ball():
                    side = self._snapped
                    wa = self._monitor_workarea()
                    self._animate_to(collapse_x(side, wa.x, wa.x+wa.width), self.get_position()[1],200)
            return False

        def _animate_to(self, tx, ty, duration=200):
            if self._anim_source is not None:
                try:
                    GLib.source_remove(self._anim_source)
                except Exception:
                    pass
                self._anim_source = None
            sx, sy = self.get_position()
            start = time.time()
            def tick():
                nonlocal tick
                t = min(1.0, (time.time()-start)/(duration/1000.0))
                e = 1-(1-t)**3
                self.move(int(round(sx + (tx-sx)*e)), int(round(sy + (ty-sy)*e)))
                if t >=1.0:
                    self._anim_source = None
                    return False
                return True
            self._anim_source = GLib.timeout_add(ANIM_MS, tick)

        def _on_ball_press(self, widget, event):
            if event.button == 1:
                x,y = self.get_position()
                self._window_pos = (x,y)
                self._press_global = (event.x_root, event.y_root)
                self._moved = False
                self._dragging = False
                if self._form == FORM_COLLAPSED and self._snapped:
                    self._expand_snap()
                self._cancel_pending_collapse()
            return False

        def _on_ball_motion(self, widget, event):
            if self._press_global is None:
                return False
            dx = event.x_root - self._press_global[0]
            dy = event.y_root - self._press_global[1]
            if not self._moved and abs(dx)<4 and abs(dy)<4:
                return False
            self._moved = True
            self._dragging = True
            if self._snapped:
                self._snapped = None
            self.move(int(self._window_pos[0]+dx), int(self._window_pos[1]+dy))
            return False

        def _on_ball_release(self, widget, event):
            was_press = self._press_global is not None
            self._press_global = None
            self._dragging = False
            if not was_press:
                return False
            if not self._moved:
                self._toggle_form()
                return False
            if self._form == FORM_COLLAPSED:
                self._maybe_snap()
            return False

        def _maybe_snap(self):
            if self._form != FORM_COLLAPSED:
                return
            wa = self._monitor_workarea()
            x,y = self.get_position()
            left_dist = x - wa.x
            right_dist = (wa.x+wa.width)-(x+self.ball.get_allocation().width)
            if left_dist < SNAP_MARGIN and left_dist <= right_dist:
                self._snap("left")
            elif right_dist < SNAP_MARGIN:
                self._snap("right")
            else:
                self._snapped = None

        def _on_ball_enter(self, widget, event):
            self._cancel_pending_collapse()
            if self._form == FORM_COLLAPSED and self._snapped and not self._dragging:
                self._expand_snap()
            return False

        def _on_ball_leave(self, widget, event):
            if self._dragging:
                return False
            if self._form == FORM_COLLAPSED and self._snapped and not self._pointer_over_ball():
                self._schedule_collapse()
            return False

        def _pointer_over_ball(self):
            try:
                display = Gdk.Display.get_default()
                seat = display.get_default_seat()
                _, px, py = seat.get_pointer().get_position()
                x,y = self.get_position()
                alloc = self.ball.get_allocation()
                return (x-6 <= px <= x+alloc.width+6 and y-6 <= py <= y+alloc.height+6)
            except Exception:
                return True

        def _report_state(self):
            if not self._form:
                return
            state = "expanded" if self._form == FORM_EXPANDED else "collapsed"
            try:
                backend.set_setting("gui_state", state)
                backend.set_setting("gui_pid", str(os.getpid()))
            except Exception:
                pass

        def _on_input_key(self, widget, event):
            if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                if not (event.state & Gdk.ModifierType.SHIFT_MASK):
                    self._send()
                    return True
            return False

        def _append_user(self, text):
            self._buf.insert(self._buf.get_end_iter(), "你："+text+"\n\n")
            self._scroll_bottom()

        def _append_assistant(self, text):
            self._buf.insert(self._buf.get_end_iter(), "AI："+text+"\n\n")
            self._scroll_bottom()

        def _append_thinking(self):
            self._buf.insert(self._buf.get_end_iter(), "思考中…\n")
            self._scroll_bottom()

        def _scroll_bottom(self):
            adj = self.view.get_vadjustment()
            if adj is not None:
                adj.set_value(adj.get_upper() - adj.get_page_size())

        def _send(self):
            if self._busy:
                return
            start_iter = self._input_buf.get_start_iter()
            end_iter = self._input_buf.get_end_iter()
            text = self._input_buf.get_text(start_iter, end_iter, False).strip()
            if not text:
                return
            self._input_buf.set_text("")
            self.lbl_clip.set_text("")
            self._append_user(text)
            self._history.append({"role":"user","content":text})
            self._busy = True
            self.btn_send.set_sensitive(False)
            self._append_thinking()
            mode = self.mode_combo.get_active_text() or MODE_QA
            history = build_messages(mode, self._history[-MAX_HISTORY*2:])
            threading.Thread(target=self._worker, args=(history,), daemon=True).start()

        def _worker(self, history):
            content, error = call_backend(history)
            GLib.idle_add(self._on_result, content, error)

        def _on_result(self, content, error):
            self._remove_thinking()
            if error:
                self._append_assistant("⚠ "+error)
            else:
                self._append_assistant(content)
                self._history.append({"role":"assistant","content":content})
            self._busy = False
            self.btn_send.set_sensitive(True)
            GLib.timeout_add(50, lambda:(self.input_edit.grab_focus(),False))

        def _remove_thinking(self):
            buf = self.view.get_buffer()
            s, e = buf.get_bounds()
            full = buf.get_text(s,e,False)
            mark = "思考中…\n"
            if full.endswith(mark):
                offset = len(full)-len(mark)
                buf.delete(buf.get_iter_at_offset(offset), e)

        def load_clipboard(self):
            try:
                clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
                text = (clip.wait_for_text() or "").strip()
            except Exception:
                return
            if not text or len(text)>8000:
                return
            if text == self._last_seen_clip:
                return
            cur = self._input_buf.get_text(self._input_buf.get_start_iter(), self._input_buf.get_end_iter(), False).strip()
            if cur == "":
                self._input_buf.set_text(text)
                self._last_seen_clip = text
                self.lbl_clip.set_text("已带入剪贴板选中文字")

        def _do_exit(self):
            if self._anim_source is not None:
                try:
                    GLib.source_remove(self._anim_source)
                except Exception:
                    pass
            if self._tray is not None:
                try:
                    self._tray.set_visible(False)
                except Exception:
                    pass
                self._tray = None
            Gtk.main_quit()

    def main():
        start = FORM_COLLAPSED
        if "--expanded" in sys.argv[1:]:
            start = FORM_EXPANDED
        app = FloatingPanel(start_form=start)
        app.show_all()
        Gtk.main()

else:
    def main():
        print("GTK3 (PyGObject) 不可用：sudo yum install gtk3 python3-gi")

if __name__ == "__main__":
    main()
