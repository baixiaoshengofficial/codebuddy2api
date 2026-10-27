# 开发与部署

后端为 Go，前端为 Vue。运行时不需要 Python，配置、凭证文件和 SQLite 请求记录沿用原格式。

## 本地运行

需要 Go 1.23+、C 编译器（SQLite 驱动使用 CGO）和 Node.js 22.8+。当前构建镜像使用 Go 1.27.3。

```bash
cp .env.example .env
# 填写 CODEBUDDY_PASSWORD
cd frontend
npm ci
npm run build
cd ..
go run ./cmd/codebuddy2api
```

访问 `http://127.0.0.1:8001`。前端开发可单独运行 `cd frontend && npm run dev`，接口转发到本地 8001 端口。`make build` 生成 `bin/codebuddy2api`，从项目根目录运行。

```bash
go vet ./...
go test -race -timeout 120s ./...
cd frontend && npm test && npm run build
```

测试使用临时凭证、临时数据库和模拟上游，不调用真实模型。覆盖自动切换、流式与非流式、工具调用、授权和重新授权、续期并发、签到、筛选导出及旧数据库迁移。

## 代码结构

| 目录 | 职责 |
| --- | --- |
| `cmd/codebuddy2api` | 启动、信号处理和健康检查命令 |
| `internal/app` | 创建依赖、装配服务和后台任务 |
| `internal/httpapi` | HTTP 鉴权、参数校验和响应展示 |
| `internal/chat` | 请求转换、SSE、工具调用聚合和逐次上游执行 |
| `internal/credential` | 账号身份、JSON 存储、凭证选择和续期 |
| `internal/oauth` | 两类产品的设备授权协议和授权会话 |
| `internal/checkin` | WorkBuddy 奖励领取、定时执行和通知 |
| `internal/ledger` | SQLite 请求明细、尝试记录、筛选与导出 |
| `internal/config` | 环境变量、配置文件和热更新 |
| `internal/modelcatalog` | 模型发现与缓存 |
| `internal/upstream`、`internal/jsonfile` | HTTP I/O 和原子文件写入 |

依赖在 `app.New` 中传入。业务包不导入 HTTP 路由或应用入口。接口定义在使用方，只描述其需要的方法；JSON、SQLite 和 HTTP 是具体实现。新增产品应增加产品配置或适配器，保持聊天、签到和授权编排独立。

CodeBuddy 与 WorkBuddy 的目录及用途分开。JWT 仅解析账号展示字段，服务访问密码使用恒定时间比较。凭证不会返回给浏览器，日志预览会脱敏。

## 请求与凭证规则

自动模式保持当前凭证，收到明确积分不足时才标记五分钟冷却并选下一个。旧版凭证接口继续提供当前账号的调用计数；查询不增加计数，指定凭证测试不改变当前选择。每次请求对同一凭证最多尝试一次；手动锁定和指定凭证测试不自动切换。普通限流、网络中断、非法或未完成的响应不会触发重发。已经输出内容后遇到积分不足，只影响后续请求。

上游使用 SSE。流式请求实时转发，非流式请求聚合文本、推理内容、工具调用和用量。开场元数据先缓冲，避免切换时把前一个账号的片段发给客户端。Responses 保留原有基础适配，流式事件在收齐回复后发送。

续期按账号串行。已过期且暂时无法续期返回 503；上游明确拒绝离线会话时标记重新授权。后台每小时检查 CodeBuddy，会话二十小时未更新时续期。WorkBuddy 在签到前检查续期。

普通授权和重新授权的会话均在内存保存，重启后需重新发起。重复轮询复用保存结果。重新授权核对账号、站点及原 Token，原位更新文件；续期和重新授权并发时通过比较后替换避免覆盖。

## 数据兼容

- `.env` 与 `config/config.json` 保留原键；文件设置优先于环境变量，只有可编辑设置会写入文件。
- `.codebuddy_creds/`、`.workbuddy_creds/` 的 JSON 及文件名继续使用，启动和查询不重写凭证。首次写入才创建目录，原子替换文件权限为 0600。
- 自动/手动模式、当前文件及冷却时间继续读取 `manager_state.json`，状态以文件名定位。
- `config/request_usage.db` 使用原表，缺失字段自动补齐，历史记录不补造上游尝试数据。
- `config/checkin_state.json` 继续保存签到状态，按北京时间调度，临时失败最多补跑三次。

升级前停止旧服务并备份以上目录和 `.env`。不要让两个实例同时写同一份数据。恢复旧镜像时可继续使用这些文件。浏览器在部署前已打开的授权会话需要重新开始。

## Docker 更新方式

镜像部署只使用 `knighttools/codebuddy2api:latest`：

```bash
git pull origin main
docker compose pull
docker compose up -d --wait
```

源码部署：

```bash
git pull origin main
CODEBUDDY_REVISION="$(git rev-parse HEAD)" \
  docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build --wait
```

镜像在构建阶段编译 Go 与前端，运行阶段仅带二进制、静态页面和系统证书。容器仍以 UID 1001 运行。Compose 在容器内固定监听 `0.0.0.0`，`CODEBUDDY_PORT` 同步用于容器端口和宿主机映射。健康检查使用 `codebuddy2api healthcheck`；自定义 Compose 中的旧 Python 健康检查需要同步替换。

服务的 `/docs` 提供参数说明，`/openapi.json` 提供接口清单。界面截图见 [README](../README.md)。
