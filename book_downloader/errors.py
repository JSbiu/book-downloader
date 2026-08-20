class DownloaderError(RuntimeError):
    """下载流程中的可预期错误。"""


class AccessBlockedError(DownloaderError):
    """服务器要求额外验证或拒绝了自动请求。"""


class ChapterExtractionError(DownloaderError):
    """页面已返回，但无法定位公开正文。"""


class DiscoveryError(DownloaderError):
    """无法从输入页面发现目录或章节列表。"""


class SearchError(DownloaderError):
    """无法读取或解析搜索结果。"""


class ConfigurationError(DownloaderError):
    """配置不完整或格式不正确。"""


class NetworkError(DownloaderError):
    """公开页面请求失败。"""
