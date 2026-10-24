"""Validate editable settings before the configuration store is changed."""

import re
from urllib.parse import urlsplit

EDITABLE_KEYS = {
    "CODEBUDDY_PASSWORD",
    "CODEBUDDY_SITE",
    "CODEBUDDY_AUTO_CHECKIN",
    "CODEBUDDY_CHECKIN_TIME",
    "CODEBUDDY_BARK_URL",
}


def validate_settings(settings):
    unknown = set(settings) - EDITABLE_KEYS
    if unknown:
        raise ValueError("不支持修改设置：" + ", ".join(sorted(unknown)))
    result = dict(settings)
    for key, value in result.items():
        if key == "CODEBUDDY_AUTO_CHECKIN":
            if not (
                type(value) is bool
                or (isinstance(value, str) and value in ("true", "false"))
            ):
                raise ValueError("自动签到必须选择开启或关闭")
        elif not isinstance(value, str):
            raise ValueError(key + " 必须为文本")
        elif key == "CODEBUDDY_PASSWORD" and not value.strip():
            raise ValueError("访问密码不能为空")
        elif key == "CODEBUDDY_SITE" and value not in ("china", "international"):
            raise ValueError("请选择有效的 CodeBuddy 站点")
        elif key == "CODEBUDDY_CHECKIN_TIME" and not re.fullmatch(
            r"(?:[01]\d|2[0-3]):[0-5]\d", value
        ):
            raise ValueError("签到时间必须为 HH:MM 格式")
        elif key == "CODEBUDDY_BARK_URL" and value:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in ("https", "http")
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Bark 地址必须为有效的 HTTP 或 HTTPS 地址")
    return result
