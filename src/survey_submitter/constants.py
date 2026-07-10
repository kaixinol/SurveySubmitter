import re

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


_HTML_SPACE_RE = re.compile(r"\s+")
_LNGLAT_PATTERN = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*$")
_INVALID_FILENAME_CHARS_RE = re.compile(r'[\\/:*?"<>|]+')


_MULTI_LIMIT_ATTRIBUTE_NAMES = (
    "max",
    "maxvalue",
    "maxValue",
    "maxcount",
    "maxCount",
    "maxchoice",
    "maxChoice",
    "maxselect",
    "maxSelect",
    "selectmax",
    "selectMax",
    "maxsel",
    "maxSel",
    "maxnum",
    "maxNum",
    "maxlimit",
    "maxLimit",
    "data-max",
    "data-maxvalue",
    "data-maxcount",
    "data-maxchoice",
    "data-maxselect",
    "data-selectmax",
)

_MULTI_LIMIT_VALUE_KEYS = (
    "max",
    "maxvalue",
    "maxcount",
    "maxchoice",
    "maxselect",
    "selectmax",
)

_MULTI_LIMIT_VALUE_KEYSET = {name.lower() for name in _MULTI_LIMIT_VALUE_KEYS}

_MULTI_MIN_LIMIT_ATTRIBUTE_NAMES = (
    "min",
    "minvalue",
    "minValue",
    "mincount",
    "minCount",
    "minchoice",
    "minChoice",
    "minselect",
    "minSelect",
    "selectmin",
    "selectMin",
    "minsel",
    "minSel",
    "minnum",
    "minNum",
    "minlimit",
    "minLimit",
    "data-min",
    "data-minvalue",
    "data-mincount",
    "data-minchoice",
    "data-minselect",
    "data-selectmin",
)

_MULTI_MIN_LIMIT_VALUE_KEYS = (
    "min",
    "minvalue",
    "mincount",
    "minchoice",
    "minselect",
    "selectmin",
    "minlimit",
)

_MULTI_MIN_LIMIT_VALUE_KEYSET = {name.lower() for name in _MULTI_MIN_LIMIT_VALUE_KEYS}

_SELECTION_KEYWORDS_CN = ("选", "選", "选择", "多选", "复选")
_SELECTION_KEYWORDS_EN = ("option", "options", "choice", "choices", "select", "choose")

_CHINESE_MULTI_LIMIT_PATTERNS = (
    re.compile(r"(?:最多|至多|不超过|不超過)\s*(?:选|選|选择|選擇)?\s*(\d+)\s*[个項项]?"),
    re.compile(r"(?:限选|限選)\s*(\d+)\s*[个項项条]?"),
)

_CHINESE_MULTI_RANGE_PATTERNS = (
    re.compile(r"(?:请[选選择擇]?|可选|可選|需选|需選|选择|選擇|勾选|勾選)\s*(\d+)\s*(?:-|－|—|–|~|～|至|到)\s*(\d+)(?:\s*[个項项条])?"),
    re.compile(r"至少\s*(\d+)\s*[个項项条]?(?:[^0-9]{0,6})(?:最多|至多|不超过|不超過)\s*(\d+)\s*[个項项条]?"),
    re.compile(r"(?:限选|限選)\s*(\d+)\s*(?:-|－|—|–|~|～|至|到)\s*(\d+)(?:\s*[个項项条])?"),
)

_CHINESE_MULTI_EXACT_PATTERNS = (
    re.compile(r"(?:请)?(?:选|選|选择|選擇|勾选|勾選)\s*(\d+)\s*[个項项条]"),
    re.compile(r"(?:必须|需|需要)\s*(?:选|選|选择|選擇|勾选|勾選)\s*(\d+)\s*[个項项条]"),
)

_CHINESE_MULTI_MIN_PATTERNS = (
    re.compile(r"(?:至少|最少|不少于)\s*(?:选|選|选择|選擇)?\s*(\d+)\s*[个項项条]"),
)

_ENGLISH_MULTI_LIMIT_PATTERNS = (
    re.compile(r"(?:select|choose|pick)\s+(?:up\s+to|at\s+most|no\s+more\s+than)\s+(\d+)", re.IGNORECASE),
    re.compile(r"(?:up\s+to|at\s+most|no\s+more\s+than)\s+(\d+)\s+(?:options?|choices?|items?)", re.IGNORECASE),
)

_ENGLISH_MULTI_RANGE_PATTERNS = (
    re.compile(r"(?:select|choose|pick)\s*(\d+)\s*(?:-|–|—|~|～|to)\s*(\d+)", re.IGNORECASE),
    re.compile(r"(?:select|choose)\s+between\s+(\d+)\s+and\s+(\d+)", re.IGNORECASE),
)

_ENGLISH_MULTI_EXACT_PATTERNS = (
    re.compile(r"(?:select|choose|pick)\s+(\d+)\s+(?:options?|choices?|items?)", re.IGNORECASE),
    re.compile(r"(?:must|need\s+to|please)\s+(?:select|choose|pick)\s+(\d+)", re.IGNORECASE),
)

_ENGLISH_MULTI_MIN_PATTERNS = (
    re.compile(r"(?:at\s+least|min(?:imum)?\s*)\s*(\d+)", re.IGNORECASE),
)
