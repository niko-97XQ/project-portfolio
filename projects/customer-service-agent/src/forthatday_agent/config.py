from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from forthatday_agent.infrastructure.deepseek_chat_model import SUPPORTED_MODELS


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_env_file(path: str | Path = PROJECT_ROOT / ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or os.environ.get(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


@dataclass(frozen=True, slots=True)
class Settings:
    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    model_timeout_seconds: int = 60
    knowledge_path: Path = PROJECT_ROOT / "knowledge"
    database_path: Path = PROJECT_ROOT / "data" / "agent_v2.db"
    catalog_path: Path = PROJECT_ROOT / "private" / "catalog"
    payment_payee_aliases: tuple[str, ...] = ()
    wechat_payment_code_path: Path = PROJECT_ROOT / "private" / "payment-codes" / "wechat.jpg"
    alipay_payment_code_path: Path = PROJECT_ROOT / "private" / "payment-codes" / "alipay.jpg"
    wecom_corp_id: str = ""
    wecom_kf_secret: str = ""
    wecom_callback_token: str = ""
    wecom_callback_aes_key: str = ""
    wecom_host: str = "127.0.0.1"
    wecom_port: int = 8080
    wecom_media_path: Path = PROJECT_ROOT / "private" / "wecom-media"
    payment_policy_path: Path = PROJECT_ROOT / "private" / "payment-policy.json"
    wecom_agent_id: int = 0
    staff_wecom_user_ids: tuple[str, ...] = ()
    wecom_default_servicer_user_id: str = ""
    app_base_url: str = ""
    staff_action_signing_key: str = ""
    staff_web_accounts: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> Settings:
        load_env_file()
        aliases = tuple(
            item.strip()
            for item in os.getenv("PAYMENT_PAYEE_ALIASES", "").split(",")
            if item.strip()
        )
        staff_user_ids = tuple(
            item.strip()
            for item in os.getenv("STAFF_WECOM_USER_IDS", "").split(",")
            if item.strip()
        )
        staff_web_accounts = tuple(
            item.strip()
            for item in os.getenv("STAFF_WEB_ACCOUNTS", "").split(",")
            if item.strip()
        )
        return cls(
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip(),
            model_timeout_seconds=int(os.getenv("MODEL_TIMEOUT_SECONDS", "60")),
            knowledge_path=Path(os.getenv("KNOWLEDGE_PATH", str(PROJECT_ROOT / "knowledge"))),
            database_path=Path(
                os.getenv("DATABASE_PATH", str(PROJECT_ROOT / "data" / "agent_v2.db"))
            ),
            catalog_path=Path(
                os.getenv("CATALOG_PATH", str(PROJECT_ROOT / "private" / "catalog"))
            ),
            payment_payee_aliases=aliases,
            wechat_payment_code_path=Path(
                os.getenv(
                    "WECHAT_PAYMENT_CODE_PATH",
                    str(PROJECT_ROOT / "private" / "payment-codes" / "wechat.jpg"),
                )
            ),
            alipay_payment_code_path=Path(
                os.getenv(
                    "ALIPAY_PAYMENT_CODE_PATH",
                    str(PROJECT_ROOT / "private" / "payment-codes" / "alipay.jpg"),
                )
            ),
            wecom_corp_id=os.getenv("WECOM_CORP_ID", "").strip(),
            wecom_kf_secret=os.getenv("WECOM_KF_SECRET", "").strip(),
            wecom_callback_token=os.getenv("WECOM_CALLBACK_TOKEN", "").strip(),
            wecom_callback_aes_key=os.getenv("WECOM_CALLBACK_AES_KEY", "").strip(),
            wecom_host=os.getenv("WECOM_HOST", "127.0.0.1").strip(),
            wecom_port=int(os.getenv("WECOM_PORT", "8080")),
            wecom_media_path=Path(
                os.getenv("WECOM_MEDIA_PATH", str(PROJECT_ROOT / "private" / "wecom-media"))
            ),
            payment_policy_path=Path(
                os.getenv(
                    "PAYMENT_POLICY_PATH",
                    str(PROJECT_ROOT / "private" / "payment-policy.json"),
                )
            ),
            wecom_agent_id=int(os.getenv("WECOM_AGENT_ID", "0")),
            staff_wecom_user_ids=staff_user_ids,
            wecom_default_servicer_user_id=(
                os.getenv("WECOM_DEFAULT_SERVICER_USER_ID", "").strip()
                or (staff_user_ids[0] if staff_user_ids else "")
            ),
            app_base_url=os.getenv("APP_BASE_URL", "").strip(),
            staff_action_signing_key=os.getenv("STAFF_ACTION_SIGNING_KEY", "").strip(),
            staff_web_accounts=staff_web_accounts,
        )

    def validate(self, *, require_model: bool = False) -> None:
        if require_model and not self.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is required")
        if self.deepseek_model not in SUPPORTED_MODELS:
            raise ValueError("DEEPSEEK_MODEL is not a supported DeepSeek V4 model")
        if self.model_timeout_seconds <= 0:
            raise ValueError("MODEL_TIMEOUT_SECONDS must be positive")

    def validate_payment_codes(self) -> None:
        if not self.wechat_payment_code_path.is_file():
            raise ValueError("WeChat payment code image is missing")
        if not self.alipay_payment_code_path.is_file():
            raise ValueError("Alipay payment code image is missing")

    def validate_wecom(self) -> None:
        if not self.wecom_corp_id:
            raise ValueError("WECOM_CORP_ID is required")
        if not self.wecom_kf_secret:
            raise ValueError("WECOM_KF_SECRET is required")
        if not self.wecom_callback_token:
            raise ValueError("WECOM_CALLBACK_TOKEN is required")
        if len(self.wecom_callback_aes_key) != 43:
            raise ValueError("WECOM_CALLBACK_AES_KEY must contain 43 characters")
        if not self.wecom_host:
            raise ValueError("WECOM_HOST is required")
        if not 1 <= self.wecom_port <= 65535:
            raise ValueError("WECOM_PORT must be between 1 and 65535")

    def validate_staff_workflow(self) -> None:
        if self.wecom_agent_id <= 0:
            raise ValueError("WECOM_AGENT_ID must be positive")
        if not self.staff_wecom_user_ids:
            raise ValueError("STAFF_WECOM_USER_IDS requires at least one user id")
        if not self.wecom_default_servicer_user_id:
            raise ValueError("WECOM_DEFAULT_SERVICER_USER_ID is required")
        if not self.app_base_url.startswith("https://"):
            raise ValueError("APP_BASE_URL must use HTTPS")
        if len(self.staff_action_signing_key) < 32:
            raise ValueError("STAFF_ACTION_SIGNING_KEY must contain at least 32 characters")
