# 架构与维护边界

## 目标

项目以“路由编排、领域服务、基础设施”三层组织。路由只负责 HTTP 输入输出、认证依赖和状态码；业务规则放在管理器或服务中；文件、SQLite 与上游 HTTP 的细节收敛到各自模块。这样可以在不启动 Web 服务的情况下测试凭证、签到和日志逻辑。

## 模块职责

| 区域 | 模块 | 责任 |
| --- | --- | --- |
| 应用装配 | `web.py` | 创建 FastAPI、注册路由、协调启动与关闭生命周期。 |
| 服务认证 | `src/auth.py` | 校验管理端与受保护 API 的服务密码。所有受保护路由复用此依赖。 |
| CodeBuddy API | `src/codebuddy_router.py`、`src/codebuddy_api_client.py`、`src/codebuddy_model_manager.py` | OpenAI 兼容协议转换、上游调用与模型发现。 |
| CodeBuddy 凭证 | `src/codebuddy_token_manager.py` | CodeBuddy 凭证存储、过期判断、手动选择与积分不足时自动切换。 |
| WorkBuddy 签到 | `src/workbuddy_token_manager.py`、`src/checkin_manager.py` | WorkBuddy 凭证存储和每日签到业务；不参与模型请求切换。 |
| 授权流程 | `src/codebuddy_auth_router.py`、`src/workbuddy_auth_router.py` | 分别处理两个产品的上游授权协议，并把获得的 token 交给对应管理器。 |
| 共享凭证工具 | `src/credential_utils.py` | 相对路径解析、JWT 显示信息解析、凭证文件命名；不依赖 FastAPI 或具体产品。 |
| 审计与设置 | `src/request_log_manager.py`、`src/settings_router.py`、`config.py` | 请求审计、运行时可修改设置和启动配置。 |

## 关键约束

- CodeBuddy 与 WorkBuddy 的凭证目录、授权入口与业务用途必须保持独立，不能交叉读取或切换。
- JWT 仅用于解析本地展示身份，不在本地验证上游 token 的签名；上游 token 的有效性由实际 API 调用决定。
- 模块导入不能产生文件、网络或数据库写入副作用。`RequestLogManager` 在首次记录请求时才创建/迁移 SQLite。
- 新增 API 时，先把可测试的领域逻辑放到服务/管理器，再在路由中完成参数解析和响应映射。
- 运行期数据（凭证、SQLite、配置快照）不能提交到 Git，也不能在日志或接口中泄露 token。

## 本地验证

```bash
venv/bin/python -m unittest discover -s tests -v
venv/bin/python -m compileall -q src web.py config.py

cd frontend
npm ci
npm run build
```

运行服务后，再检查：

```bash
curl -fsS http://127.0.0.1:8001/health
```

需要验证受保护端点时，使用 `CODEBUDDY_PASSWORD` 作为 Bearer Token；不要把真实凭证写入测试、命令历史或文档。

## 后续重构准则

`src/codebuddy_router.py` 是协议适配层中最大的模块。后续应按“流式转换、非流式聚合、请求审计、Responses 适配、凭证管理端点”拆分为内部模块，并为每一块先补回归测试；不要在同一提交中同时改写协议行为和目录结构。
