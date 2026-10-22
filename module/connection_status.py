import asyncio
import logging

import aiohttp


class ConnectionStatusLogger:
    """讓週期性輪詢只在每次斷線期間輸出一次同類錯誤。"""

    def __init__(self, label):
        self.label = label
        self._failed = False
        self._logged_error_types = set()

    @staticmethod
    def _error_summary(error):
        if isinstance(error, aiohttp.ClientConnectorCertificateError):
            return "TLS 憑證驗證失敗"
        if isinstance(error, asyncio.TimeoutError):
            return "連線逾時"
        if isinstance(error, aiohttp.ClientConnectorDNSError):
            return "DNS 解析失敗"
        if isinstance(error, aiohttp.ClientConnectionError):
            return "無法連線到伺服器"
        return type(error).__name__

    def failed(self, error):
        self._failed = True
        error_type = type(error)
        if error_type in self._logged_error_types:
            return

        self._logged_error_types.add(error_type)
        logging.error(
            f"❌ [{self.label}] 連線失敗：{self._error_summary(error)}"
            "（連線恢復前不再重複輸出）"
        )

    def succeeded(self):
        if self._failed:
            logging.info(f"✅ [{self.label}] 連線已恢復")
        self._failed = False
        self._logged_error_types.clear()
