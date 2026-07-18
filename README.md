# CodeBuddy2API

将 CodeBuddy 官方 API 包装成一个功能强大、与 OpenAI API 格式兼容的服务。本项目可以直接调用 CodeBuddy 官方 API，并为所有标准客户端提供统一的接口。

## 🌟 功能特性

- 🔌 **OpenAI 兼容接口**：统一通过 `/codebuddy/v1` 提供模型列表、Chat Completions 和 Responses 兼容端点，可直接被 cc-switch 等工具使用。
- 🔄 **智能响应处理**：即使 CodeBuddy 原生仅支持流式响应，本服务也能为客户端智能处理**非流式**请求，并在后端自动完成“流式转非流式”的响应包装。
- ⚡ **高性能**：完全基于 FastAPI 和 `asyncio` 构建，支持高并发异步请求。
- 🔐 **双重认证机制**：
    - **服务访问认证**：通过环境变量设置密码，保护整个代理服务。
    - **CodeBuddy 官方认证**：在后端安全地管理和使用 CodeBuddy 的 `Bearer Token`。
- 🔄 **凭证自动轮换**：支持在 `.codebuddy_creds` 目录中配置多个 CodeBuddy 认证凭证，服务会自动轮换使用，有效提高可用性和分担请求压力。
- 🎁 **每日自动签到**：通过独立的 WorkBuddy 授权获取签到凭证，按北京时间定点领取 Credits，并将结果推送到 Bark。
- 🌐 **Web 管理界面**：内置一个美观、易用的 Web UI，方便用户管理凭证、测试 API 和查看服务状态。

## 🚀 快速开始

### 1. 前置要求

- Python 3.8 或更高版本
- Git

### 2. 下载和安装

首先，克隆本项目到本地：
```bash
git clone https://github.com/xueyue33/codebuddy2api.git
cd codebuddy2api
```

然后，运行启动脚本。此脚本会自动创建 Python 虚拟环境并安装所有必需的依赖。

**Windows:**
```bash
start.bat
```

**Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python web.py
```

### 3. 配置环境变量

项目启动需要一些基本配置。请将根目录下的 `.env.example` 文件复制一份并重命名为 `.env`：

```bash
cp .env.example .env
```

然后，用你的文本编辑器打开 `.env` 文件，**至少需要设置以下必需的变量**：

```dotenv
# (必需) API服务的访问密码，客户端连接时需要提供此密码
CODEBUDDY_PASSWORD=your_secret_password_for_this_service
```

### 4. 添加 CodeBuddy 认证凭证

为了让服务能够代理请求，你至少需要添加一个有效的 CodeBuddy 认证凭证。本项目提供了极为便捷的**自动化认证**方式。

**推荐方式：使用 Web 管理界面自动获取**

1.  启动服务后，使用浏览器访问 `http://127.0.0.1:8001` (或你自定义的地址)。
2.  输入你在 `.env` 文件中设置的 `CODEBUDDY_PASSWORD` 登录管理面板。
3.  进入 “**凭证管理**” 标签页。
4.  点击 **自动获取认证** 卡片中的 “**开始认证**” 按钮。
5.  系统会自动生成一个 CodeBuddy 的官方登录链接。请点击 “**打开链接**” 按钮。
6.  在新打开的 CodeBuddy 页面中完成登录授权。
7.  **完成！** 登录成功后，请关闭登录页面。本服务会自动检测到登录状态，并为你获取、解析和保存新的认证凭证。你只需点击 “**刷新列表**” 即可看到新添加的凭证。


### 5. 启动服务

一切准备就绪后，再次运行启动脚本即可启动服务：

**Windows:**
```bash
start.bat
```

**直接运行:**
```bash
# 确保你已在虚拟环境中 (source venv/bin/activate)
python web.py
```

服务启动后，你就可以开始使用了！

### 6. 一键 Docker 部署到调试服务器

仓库提供了 `Makefile` 和 `scripts/deploy_docker.sh`，可将当前代码打包上传到服务器并执行 `docker compose up -d --build`。

```bash
DEPLOY_HOST=192.168.100.3 \
DEPLOY_USER=root \
DEPLOY_PASSWORD='你的SSH密码' \
CODEBUDDY_SITE=china \
make deploy
```

部署脚本会读取本地 `.env` 中的 `CODEBUDDY_PASSWORD` 并写入服务器 `.env`。默认部署目录为 `/opt/codebuddy2api`，服务地址为 `http://192.168.100.3:8001`。如果服务器尚未安装 Docker，可运行 `DEPLOY_PASSWORD='你的SSH密码' make deploy-install-docker` 自动安装。常用调试命令：`make ps`、`make logs`、`make restart`、`make health`。

## ⚙️ API 使用

### 认证

除模型列表外，对本服务的 API 请求都需要使用你在 `.env` 文件里设置的 `CODEBUDDY_PASSWORD`。对 OpenAI SDK 来说，这个值就是 `api_key`；对 HTTP 请求来说，它就是 Bearer Token。`GET /codebuddy/v1/models` 可直接访问，方便 cc-switch 等客户端发现模型。

`Authorization: Bearer your_secret_password_for_this_service`

外部客户端统一使用 `/codebuddy/v1`，例如 `http://192.168.100.3:8001/codebuddy/v1`。

### cc-switch / Codex 配置

在 cc-switch 中添加自定义 OpenAI 兼容 Provider 时：

- API Key：填写 `.env` 中的 `CODEBUDDY_PASSWORD`。
- Endpoint URL：填写 `http://你的服务地址:8001/codebuddy/v1`。
- Fetch Models：cc-switch 会请求 `GET /codebuddy/v1/models`，本服务会返回 OpenAI 兼容模型列表。
- Codex 使用：本服务同时提供 `/codebuddy/v1/chat/completions` 和轻量 `/codebuddy/v1/responses` 兼容端点；如果使用 cc-switch 的 Chat Completions 本地路由，请让它转到 `/codebuddy/v1/chat/completions`。

### 客户端集成示例

你可以将任何支持 OpenAI API 的客户端指向本服务。

**Python 客户端:**
```python
import openai

client = openai.OpenAI(
    api_key="your_secret_password_for_this_service",
    base_url="http://192.168.100.3:8001/codebuddy/v1"
)

# 非流式请求
response = client.chat.completions.create(
    model="<model-id-from-/codebuddy/v1/models>",
    messages=[
        {"role": "user", "content": "你好，2+2等于几？"}
    ]
)
print(response.choices[0].message.content)

# 流式请求
stream = client.chat.completions.create(
    model="<model-id-from-/codebuddy/v1/models>",
    messages=[
        {"role": "user", "content": "写一个Python的Hello World脚本"}
    ],
    stream=True
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")

```

**curl 命令行示例:**
```bash
# 非流式请求
curl -sS -X POST 'http://192.168.100.3:8001/codebuddy/v1/chat/completions' \
  -H 'Authorization: Bearer your_secret_password_for_this_service' \
  -H 'Content-Type: application/json' \
  --data-raw '{"model":"<model-id-from-/codebuddy/v1/models>","messages":[{"role":"user","content":"Hello, what is 2+2?"}]}'

# 流式请求
curl -sS -X POST 'http://192.168.100.3:8001/codebuddy/v1/chat/completions' \
  -H 'Authorization: Bearer your_secret_password_for_this_service' \
  -H 'Content-Type: application/json' \
  --data-raw '{"model":"<model-id-from-/codebuddy/v1/models>","messages":[{"role":"user","content":"Write a Python hello world script"}],"stream":true}'
```

## 📝 API 端点

- `GET /codebuddy/v1`: 轻量探测接口，供 cc-switch 等工具测速。
- `GET /codebuddy/v1/models`: OpenAI 兼容模型列表接口，按当前 `CODEBUDDY_SITE` 动态获取 CodeBuddy 官方模型列表。
- `GET /codebuddy/v1/chat/completions`: 轻量探测接口，供 cc-switch 等工具测速。
- `POST /codebuddy/v1/chat/completions`: OpenAI 兼容聊天接口。
- `GET /codebuddy/v1/responses`: 轻量探测接口，供 cc-switch 等工具测速。
- `POST /codebuddy/v1/responses`: 最小 Responses 兼容接口，用于 Codex/cc-switch 连通性探测和简单请求。
- `GET /codebuddy/v1/embeddings`: 轻量探测接口，供向量查询客户端测速。
- `POST /codebuddy/v1/embeddings`: OpenAI 兼容 Embeddings 接口，使用本地确定性 hashing embedding，适合向量库连通和基础检索。
- `GET /codebuddy/v1/credentials`: （需要认证）在 Web UI 中用于列出所有凭证。
- `POST /codebuddy/v1/credentials`: （需要认证）在 Web UI 中用于添加新凭证。
- `GET /api/checkin`: （需要认证）查看自动签到计划及逐账号状态。
- `POST /api/checkin/run`: （需要认证）立即检查并领取所有账号的待领签到奖励。
- `GET /workbuddy/auth/start`: （需要认证）启动 WorkBuddy 签到授权。
- `POST /workbuddy/auth/poll`: （需要认证）轮询 WorkBuddy 授权结果并保存凭证。
- `GET /workbuddy/credentials`: （需要认证）列出已授权的 WorkBuddy 签到账号。
- `GET /health`: 服务的健康检查端点。

## 🔧 项目结构

```
codebuddy2api/
├── src/                           # 源代码目录
│   ├── auth.py                    # 服务访问认证模块
│   ├── codebuddy_api_client.py    # 封装了与CodeBuddy官方API的通信
│   ├── codebuddy_auth_router.py   # CodeBuddy OAuth2 认证路由
│   ├── codebuddy_token_manager.py # CodeBuddy凭证加载与轮换管理器
│   ├── checkin_manager.py          # WorkBuddy 自动签到管理器
│   ├── workbuddy_auth_router.py    # WorkBuddy 签到授权
│   ├── workbuddy_token_manager.py  # WorkBuddy 签到凭证存储
│   ├── codebuddy_router.py        # 核心API路由 (v1) - 已重构优化
│   ├── frontend_router.py         # Web管理界面的路由
│   ├── settings_router.py         # 设置管理路由
│   ├── usage_stats_manager.py     # 使用统计管理器
│   └── keyword_replacer.py        # 关键词替换模块
├── frontend/
│   ├── src/                        # Vue 管理端源码
│   └── vite.config.js              # Vite 构建配置
├── .codebuddy_creds/              # 存放CodeBuddy凭证的目录 (Git会忽略其中的文件)
├── web.py                         # FastAPI服务主入口
├── config.py                      # 环境变量配置管理
├── requirements.txt               # Python依赖列表
├── .env.example                   # 环境变量示例文件
├── start.bat                      # Windows一键启动脚本
├── docker-compose.yml             # Docker Compose 配置
├── Dockerfile                     # Docker 镜像构建文件
├── entrypoint.sh                  # Docker 容器入口脚本
└── README.md                      # 本文档
```

## ⚙️ 配置选项

启动配置由 `.env` 或环境变量提供。管理端只允许修改可热加载的设置，改动会
立即生效并按需写入 `config/config.json`。监听地址、端口、日志级别和凭证目录
在管理端只读，始终由启动配置管理，不会被管理端保存的快照覆盖。

| 环境变量 | 默认值 | 说明 |
| ---------------------- | --------------------- | ---------------------------------------------------------- |
| `CODEBUDDY_PASSWORD` | - | **(必需)** 访问此API服务的密码。 |
| `CODEBUDDY_HOST` | `127.0.0.1` | 服务监听的主机地址。 |
| `CODEBUDDY_PORT` | `8001` | 服务监听的端口。 |
| `CODEBUDDY_SITE` | `china` | CodeBuddy 站点，可选 `international`（国际站）或 `china`（国内站）；API 端点会按站点自动选择。 |
| `CODEBUDDY_LOG_LEVEL` | `INFO` | 日志级别，可选 `DEBUG`, `INFO`, `WARNING`, `ERROR`。 |
| `CODEBUDDY_ROTATION_COUNT` | `1` | 凭证轮换计数，每 N 次请求后切换凭证。 |
| `CODEBUDDY_AUTO_CHECKIN` | `true` | 是否自动检查并领取每日签到奖励。 |
| `CODEBUDDY_CHECKIN_TIME` | `11:00` | 每日自动签到时间（北京时间 HH:MM）。 |
| `CODEBUDDY_BARK_URL` | 空 | 签到完成后推送结果的 Bark URL；留空则不推送。 |

CodeBuddy 和 WorkBuddy 凭证分别固定存放在 `.codebuddy_creds` 与
`.workbuddy_creds`。Docker 部署时如需从宿主机查看或备份凭证，请修改
`docker-compose.yml` 的卷来源路径，容器内目标路径保持不变。

## 🐛 故障排除

- **"No valid CodeBuddy credentials found"**:
  - 确保你已经在 `.codebuddy_creds` 目录下添加了至少一个有效的凭证 JSON 文件。
  - 推荐使用 Web UI 添加，以确保格式正确。

- **"API error: 401" / "API error: 403" (来自 CodeBuddy)**:
  - 这通常意味着你的 CodeBuddy `Bearer Token` 无效或已过期。请通过官网重新获取一个新的 Token，并在 Web UI 中更新。

- **"Invalid password"**:
  - 这意味着你访问本服务时，请求头中提供的 Bearer Token 与你在 `.env` 文件中设置的 `CODEBUDDY_PASSWORD` 不匹配。

- **需要查看详细日志**:
  - 在 `.env` 文件中设置 `CODEBUDDY_LOG_LEVEL=DEBUG`，然后重启服务。
