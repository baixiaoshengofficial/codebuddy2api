# CodeBuddy2API

Go 编写的 CodeBuddy OpenAI 兼容 API 代理，账号授权、请求记录和 WorkBuddy 签到在网页里管理。

## 安装

需要 Git、Docker 和 Docker Compose v2。镜像为 `knighttools/codebuddy2api:latest`，支持 `linux/amd64` 和 `linux/arm64`，拉取时自动匹配架构。

```bash
git clone --branch main https://github.com/baixiaoshengofficial/codebuddy2api.git
cd codebuddy2api
cp .env.example .env
```

修改 `.env` 中的 `CODEBUDDY_PASSWORD`，设置自己的访问密码，然后启动：

```bash
docker compose up -d --wait
```

打开 `http://127.0.0.1:8001`，用刚设置的密码登录。远程部署时换成服务器地址。

数据保存在 `config/`、`.codebuddy_creds/` 和 `.workbuddy_creds/`。迁移前停止服务，连同 `.env` 一起备份。

## 添加账号

进入“凭证管理”，选择 CodeBuddy 或 WorkBuddy，点“添加凭证”，打开授权页登录。凭证过期后，点对应条目的“重新授权”，登录原账号后更新原条目。

自动模式保持使用当前凭证，积分不足时换下一个。回复已经开始输出，或请求遇到超时、断线时，不重发当前请求。

WorkBuddy 需要单独授权，用于签到。默认每天北京时间 `11:00` 执行，也可以手动签到。签到时间和 Bark 推送地址在“服务设置”里修改。

“请求明细”里可以查上游尝试记录、筛选请求和导出 CSV。搜索按回车提交，自定义日期选好后点“应用时间范围”。网页里的模型测试会调用真实接口，可能消耗积分。

## 接入客户端

| 配置 | 填写内容 |
| --- | --- |
| Base URL | `http://127.0.0.1:8001/codebuddy/v1` |
| API Key | 当前访问密码 |
| 模型 | 从 `GET /codebuddy/v1/models` 返回的列表中选择 |

对话用 `POST /codebuddy/v1/chat/completions`，可用流式、非流式和工具调用。鉴权头为 `Authorization: Bearer <访问密码>`。

`POST /codebuddy/v1/responses` 是基础适配，流式事件也要等上游回复收齐后才发送。接口参数见服务的 `/docs`。

## 更新

```bash
git pull origin main
docker compose pull
docker compose up -d --wait
```

`restart` 只会重启旧容器，不更新镜像。从源码构建的命令见[开发文档](docs/architecture.md#docker-更新方式)。

截图：[请求明细](docs/screenshots/requests-filters-desktop.png) · [手机](docs/screenshots/requests-filters-mobile.png) · [WorkBuddy](docs/screenshots/workbuddy-states-desktop.png)

其他说明：[配置示例](.env.example) · [开发与测试](docs/architecture.md)
