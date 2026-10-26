# CodeBuddy2API

将 CodeBuddy 接入 OpenAI 兼容接口，附带凭证管理、请求明细和 WorkBuddy 签到。

- 支持流式、非流式对话和工具调用，提供基础 Responses 适配。
- 多凭证积分不足时切换，支持自动续期、手动锁定和重新授权。
- 管理端支持请求筛选、逐次尝试记录、CSV 导出和手机访问。

## 部署

需要 Git、Docker 和 Docker Compose v2 或更新版本。默认镜像为 `knighttools/codebuddy2api:latest`，支持 `linux/amd64`。

```bash
git clone --branch main https://github.com/baixiaoshengofficial/codebuddy2api.git
cd codebuddy2api
cp .env.example .env
```

编辑 `.env`，设置访问密码：

```dotenv
CODEBUDDY_PASSWORD=请替换为你自己的密码
```

启动后打开 `http://127.0.0.1:8001`，使用访问密码登录：

```bash
docker compose up -d --wait
```

配置和账号保存在 `config/`、`.codebuddy_creds/`、`.workbuddy_creds/`，重建容器会保留这些目录。

## 更新

```bash
git pull origin main
docker compose pull
docker compose up -d --wait
```

只执行 `git pull` 或 `docker compose restart` 不会更新现有镜像。源码构建见[开发说明](docs/architecture.md#docker-更新方式)。

## 使用

- **添加账号**：进入“凭证管理”，选择 CodeBuddy 或 WorkBuddy，点击“添加凭证”完成浏览器授权。
- **重新授权**：点击过期条目的“重新授权”，使用原账号登录，成功后更新原条目。
- **凭证切换**：自动模式保持当前凭证，积分不足且尚未输出时切换重试；超时或断线不自动重发。
- **WorkBuddy**：单独授权，仅用于签到。默认每天北京时间 `11:00` 执行，也可手动签到。
- **请求明细**：下拉筛选即时生效，搜索和自定义时间需提交；CSV 按已提交条件导出。

站点、密码、签到时间和 Bark 推送可在“服务设置”修改。模型测试会调用真实接口，可能消耗积分。

## 接口

| 客户端配置 | 值 |
| --- | --- |
| Base URL | `http://127.0.0.1:8001/codebuddy/v1` |
| API Key | 管理端当前访问密码 |
| 模型列表 | `GET /codebuddy/v1/models` |
| 对话 | `POST /codebuddy/v1/chat/completions` |
| Responses | `POST /codebuddy/v1/responses` |

聊天接口使用 `Authorization: Bearer <访问密码>`。Responses 是基础适配，流式事件会在上游回复收齐后发送。完整接口参数见服务的 `/docs`。

## 界面与文档

[请求明细](docs/screenshots/requests-filters-desktop.png) · [手机界面](docs/screenshots/requests-filters-mobile.png) · [WorkBuddy 账号](docs/screenshots/workbuddy-states-desktop.png)

- [配置示例](.env.example)
- [开发、测试与实现说明](docs/architecture.md)
