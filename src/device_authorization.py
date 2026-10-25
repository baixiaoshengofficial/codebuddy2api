"""Provider-independent login outcomes; upstream credentials stay on the server."""


class AuthorizationError(ValueError):
    def __init__(self, status, error, message):
        super().__init__(message)
        self.status = status
        self.error = error


def public_authorization_start(result):
    if not result.get("success"):
        raise AuthorizationError(502, "auth_start_failed", "启动授权失败，请稍后重试")
    if not result.get("auth_state") or not result.get("verification_uri_complete"):
        raise AuthorizationError(502, "invalid_auth_response", "上游未返回完整授权链接")
    allowed = (
        "success",
        "auth_state",
        "verification_uri_complete",
        "verification_uri",
        "expires_in",
        "interval",
        "platform",
        "method",
        "status",
    )
    return {key: result[key] for key in allowed if key in result}


class DeviceAuthorization:
    def __init__(self, start_login, poll_login, save_token):
        self.start_login = start_login
        self.poll_login = poll_login
        self.save_token = save_token

    async def start(self):
        return public_authorization_start(await self.start_login())

    async def poll(self, auth_state):
        if not auth_state:
            raise AuthorizationError(
                400, "missing_parameters", "缺少必要的参数：auth_state"
            )
        result = await self.poll_login(auth_state)
        if result.get("status") == "pending":
            raise AuthorizationError(400, "authorization_pending", "等待用户登录授权")
        if result.get("status") != "success":
            raise AuthorizationError(400, "auth_error", "授权失败或已过期，请重新开始")
        token_data = result.get("token_data") or {}
        if not (token_data.get("access_token") or token_data.get("bearer_token")):
            raise AuthorizationError(
                502, "invalid_token_response", "上游未返回有效凭证"
            )
        if not await self.save_token(token_data):
            raise AuthorizationError(500, "save_failed", "保存凭证失败，请重新尝试授权")
        return {"saved": True, "message": "授权成功，凭证已保存"}
