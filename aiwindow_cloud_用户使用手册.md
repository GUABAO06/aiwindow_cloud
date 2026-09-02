---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 06c55cafedf6bf51e8bda6d121982815_cb3fd3a3a6b911f1b87f525400461939
    ReservedCode1: fdDpqmwrhJerySAZtp8JIr6zcvD5riu29/qE2OarcqRpDI9lRlK9ywdnmofdOC98ek63c0r8JqD4AMYAk5KLSY/OdSlvJBsr0Db54H5YphBQiPmBFLIsQaTD99C2W1fwuYVpT0tV0bYUIHHbKUFAuxqNIMoRpcK42S8bII9ntem2BOZ3/rYc/VPszFY=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 06c55cafedf6bf51e8bda6d121982815_cb3fd3a3a6b911f1b87f525400461939
    ReservedCode2: fdDpqmwrhJerySAZtp8JIr6zcvD5riu29/qE2OarcqRpDI9lRlK9ywdnmofdOC98ek63c0r8JqD4AMYAk5KLSY/OdSlvJBsr0Db54H5YphBQiPmBFLIsQaTD99C2W1fwuYVpT0tV0bYUIHHbKUFAuxqNIMoRpcK42S8bII9ntem2BOZ3/rYc/VPszFY=
---

# aiwindow_cloud 智能云端助手 用户使用手册

- 软件版本：v1.6.8
- 适用系统：银河麒麟、统信 UOS、openEuler、Debian / Ubuntu 等国产化 Linux 桌面
- 文档版本：v1.0

---

## 一、产品简介

aiwindow_cloud 是一个装在国产电脑桌面上的「划词式 AI 助手」：屏幕上有一个可以拖来拖去的小圆球（悬浮球），点开它就能跟 AI 聊天、让 AI 解释代码、帮你润色文字。

它的小特点：

- **装完开机自动启动**，不用手动配置，开箱即用；
- **选中文字就能问**，不用复制粘贴；
- **关闭窗口不退出**，只是藏起来挂在后台，随叫随到；
- **密钥加密保存**，不担心被别人看到明文。

---

## 二、安装前准备

开始安装前，先确认三件事：

| 序号 | 确认项 | 说明 |
|---|---|---|
| 1 | 系统版本 | 银河麒麟 / 统信 UOS / openEuler 等国产 Linux 桌面系统 |
| 2 | 能上网 | 首次部署需联网安装依赖，AI 对话也需要联网 |
| 3 | 已拿到安装包 | 得到 `aiwindow-cloud-1.6.8-…` 的安装包（rpm 或 deb） |

> 提示：如果不能联网安装依赖，可请系统管理员提前预装以下软件包：
> `gtk3`、`python3-gi`（或 `python3-gobject`）、`cairo-gobject`、`rpm-build`、`openssl`。

---

## 三、安装部署

### 3.1 一键安装（推荐）

拿到安装包后，在终端（命令行窗口，桌面右键 →「打开终端」）中输入对应命令：

**rpm 系（银河麒麟服务器版、openEuler）：**

```
sudo rpm -ivh aiwindow-cloud-1.6.8-1.loongarch64.rpm
```

**deb 系（统信 UOS、Debian、Ubuntu）：**

```
sudo dpkg -i aiwindow-cloud_1.6.8_loongarch64.deb
```

看到类似 `100%` 且无报错，即安装成功。系统已自动设置开机自启动，无需再做其他设置。

### 3.2 手动构建安装（可选）

如果你拿到的是源码工程 `aiwindow_cloud`，也可以自己打安装包：

1. 进入工程目录：`cd aiwindow_cloud`
2. 安装构建依赖：`sudo yum install -y gtk3 python3-gi cairo-gobject rpm-build`
3. 执行一键构建：`bash build.sh`（默认生成 rpm；需要 deb 时执行 `AIWINDOW_DEB=1 bash build.sh`）
4. 构建成功后目录内会生成安装包，再按 3.1 安装即可

> 提示：如需开箱预置密钥，可在构建前加参数 `AIWINDOW_API_KEY="你的密钥" bash build.sh`。

---

## 四、首次启动与配置

### 4.1 启动

重启电脑并登录桌面（或直接运行应用后），屏幕边缘会出现一个**小圆球**——这就是 aiwindow_cloud，已经在后台运行。

### 4.2 首次使用：确认/配置密钥

默认安装包已内置智谱种子密钥（首次运行自动导入），多数情况下可直接使用。如需更换或自定义：

1. 左键单击悬浮球，展开聊天面板；
2. 点聊天面板顶部的**模式下拉框 → 选择「设置…」**，进入设置界面；
3. 按表填写：

| 填写项 | 说明 | 默认值（可不改） |
|---|---|---|
| API Key | AI 服务商提供的密钥（从服务商控制台获取） | 已预置 |
| base_url | 连接 AI 服务的网址 | `https://open.bigmodel.cn/api/paas/v4` |
| model | 使用的模型名称 | `glm-4.7-flash` |
| timeout | 请求超时时间（秒） | 默认值即可 |

4. 设置页会显示「模型连接状态」：绿色=已连接、红色=鉴权失败或网络异常、灰色=未配置密钥。保存后可点「刷新」按钮再次检测。

### 4.3 验证是否可用

回到聊天面板，输入一句简单的话（比如"你好"），按回车。**能收到 AI 回复**，即部署成功。

---

## 五、日常使用

### 5.1 悬浮球操作

| 操作 | 效果 |
|---|---|
| 左键单击小圆球 | 展开/收起聊天面板 |
| 拖动小圆球 | 移动到任意位置，松手自动吸附屏幕边缘 |
| 关闭窗口 / Alt+F4 | 仅隐藏，后台继续运行（不退出） |
| 系统托盘图标 | 单击切换显隐、右键菜单可退出 |
| 设置页「退出应用」 | 弹出确认框，确认后彻底退出 |

### 5.2 对话模式

进入聊天面板后，通过**模式下拉框**切换：

| 模式 | 用途 |
|---|---|
| 问答 | 日常提问，直接回答 |
| 代码解释 | 粘贴代码，解析作用、逻辑与注意事项 |
| 文本润色 | 粘贴文字，改写得更通顺、专业、简洁 |

### 5.3 快捷键

- **Enter**：发送消息
- **Shift+Enter**：换行
- **划词自动带入**：先在别处复制一段文字，点进输入框即自动带入，无需手动粘贴

### 5.4 设置与日志

- 设置页可查看版本信息、模型连接状态、云端调用日志；
- 日志可查看最近调用记录（状态/摘要/时间），支持一键清空。

---

## 六、退出与卸载

### 6.1 彻底退出

推荐两种方式（任选其一）：

- **系统托盘**右键菜单 →「退出」
- 展开面板 → **设置… →「退出应用」** → 确认

> 注意：直接关闭窗口只是隐藏，不会退出程序。

### 6.2 卸载

```
sudo rpm -e aiwindow      # rpm 系
sudo dpkg -r aiwindow     # deb 系
```

- 卸载会一并关闭开机自启动；
- 密钥、聊天记录等数据会保留（位于 `~/.config/aiwindow` 与 `/etc/aiwindow`、`/var/lib/aiwindow`），不会因卸载丢失；如需彻底清除，请先备份再手动删除。

---

## 七、常见问题排查

| 现象 | 处理方法 |
|---|---|
| 重启后悬浮球没出现 | 确认已登录**图形桌面**；终端运行 `systemctl status aiwindow-backend` 查看服务状态 |
| 点聊天没反应 / 提示未配置密钥 | 进入「设置…」，确认 API Key 已填写且正确，base_url、model 用默认值 |
| 连接状态显示红色 | 检查密钥是否有效、网络是否通畅；可点「刷新」重试 |
| 提示「无权访问模型」 | 密钥有效但缺少该模型权限，请到服务商控制台开通对应模型 |
| 回答突然中断/异常 | 检查网络与模型额度；等待片刻或点「刷新」后重试 |
| 卸载后想重装 | 数据仍保留，直接重新安装即可，无需重复配置 |

---

## 八、技术参数速览

| 项目 | 说明 |
|---|---|
| 支持架构 | LoongArch64 / arm64 / amd64 |
| 常驻内存 | 目标 ≤300MB（systemd 限制 150M） |
| 数据目录 | `~/.config/aiwindow`（可迁移自旧路径） |
| 敏感数据 | AES-256 加密存储，不落明文、不监听端口 |
| 默认模型 | glm-4.7-flash（智谱免费模型），可切换多平台 |

---

如仍有疑问，请记录设置页中的版本信息与调用日志，便于技术支持快速定位。

