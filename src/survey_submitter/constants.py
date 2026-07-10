AUTO_SAVE_LOGS_SETTING_KEY = "auto_save_logs"
AUTO_SAVE_LOG_RETENTION_COUNT_SETTING_KEY = "auto_save_log_retention_count"
DEFAULT_AUTO_SAVE_LOGS = True
DEFAULT_AUTO_SAVE_LOG_RETENTION_COUNT = 10
AUTO_SAVE_LOG_RETENTION_OPTIONS = (3, 5, 10, 20, 30, 50)




USER_AGENT_PRESETS = {
    "pc_web": {
        "label": "电脑网页端",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    },
    "mobile_android": {
        "label": "安卓手机浏览器",
        "ua": "Mozilla/5.0 (Linux; Android 16; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    },
    "wechat_android": {
        "label": "安卓微信端",
        "ua": "Mozilla/5.0 (Linux; Android 16; Pixel 8 Build/BP22.250124.009; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/121.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.43.2460(0x28002B3B) Process/appbrand0 WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64",
    },
}


DEFAULT_USER_AGENT = USER_AGENT_PRESETS["pc_web"]["ua"]


DEFAULT_HTTP_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "close",
}


LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_BUFFER_CAPACITY = 2000


PROXY_MAX_PROXIES = 80
PROXY_HEALTH_CHECK_URL = "https://www.wjx.cn"
PROXY_HEALTH_CHECK_TIMEOUT = 15
PROXY_TTL_GRACE_SECONDS = 20
PROXY_MINUTE_OPTIONS = (1, 3, 5, 10, 15, 30)
PROXY_SOURCE_CUSTOM = "custom"

PROXY_POOL_QUALITY = "quality"

DEFAULT_FILL_TEXT = "无"


DIMENSION_UNGROUPED = "未分组"
