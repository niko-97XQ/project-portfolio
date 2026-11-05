from __future__ import annotations

import sys
import threading
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from forthatday_agent.application.payment_verification import (  # noqa: E402
    PaymentVerificationService,
)
from forthatday_agent.application.action_executor import ActionExecutor  # noqa: E402
from forthatday_agent.application.staff_access import (  # noqa: E402
    StaffActionSigner,
    StaffPasswordVerifier,
    StaffSessionSigner,
)
from forthatday_agent.application.staff_appointments import (  # noqa: E402
    PaymentCodes,
    StaffAppointmentService,
)
from forthatday_agent.application.staff_web import StaffTaskWebApp  # noqa: E402
from forthatday_agent.application.dialogue_engine import UnifiedDialogueEngine  # noqa: E402
from forthatday_agent.application.message_pipeline import ReliableMessagePipeline  # noqa: E402
from forthatday_agent.application.service_runtime import ServiceRuntime  # noqa: E402
from forthatday_agent.application.wecom_callback import (  # noqa: E402
    WeComCallbackReceiver,
    WeComCallbackWorker,
)
from forthatday_agent.application.wecom_sync import WeComSyncService  # noqa: E402
from forthatday_agent.config import Settings  # noqa: E402
from forthatday_agent.infrastructure.payment_policy_loader import (  # noqa: E402
    load_payment_policy,
)
from forthatday_agent.infrastructure.deepseek_chat_model import DeepSeekChatModel  # noqa: E402
from forthatday_agent.infrastructure.catalog_store import CatalogStore  # noqa: E402
from forthatday_agent.infrastructure.catalog_knowledge import (  # noqa: E402
    CatalogKnowledgeProvider,
)
from forthatday_agent.infrastructure.markdown_knowledge import (  # noqa: E402
    MarkdownKnowledgeProvider,
)
from forthatday_agent.infrastructure.rapidocr_provider import RapidOcrProvider  # noqa: E402
from forthatday_agent.infrastructure.sqlite_message_store import (  # noqa: E402
    SqliteMessageStore,
)
from forthatday_agent.infrastructure.wecom_api import (  # noqa: E402
    WeComApiClient,
    WeComCustomerServiceGateway,
    WeComNativeHandoffGateway,
)
from forthatday_agent.infrastructure.wecom_callback_crypto import (  # noqa: E402
    WeComCallbackCrypto,
)
from forthatday_agent.infrastructure.wecom_http import (  # noqa: E402
    create_wecom_callback_server,
)


def main() -> None:
    settings = Settings.from_env()
    server, runtime = build_service(settings)
    stop = threading.Event()
    worker_thread = threading.Thread(
        target=_run_worker,
        args=(runtime, stop),
        name="forthatday-service-worker",
        daemon=True,
    )
    worker_thread.start()
    print(
        f"WeCom callback listening on http://{settings.wecom_host}:{settings.wecom_port}"
        "/wecom/callback"
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        worker_thread.join(timeout=3)


def build_service(
    settings: Settings,
    *,
    host: str | None = None,
    port: int | None = None,
):
    settings.validate(require_model=True)
    settings.validate_wecom()
    settings.validate_staff_workflow()
    settings.validate_payment_codes()
    store = SqliteMessageStore(settings.database_path)
    catalog_store = CatalogStore(settings.database_path, catalog_root=settings.catalog_path)
    signer = StaffActionSigner(settings.staff_action_signing_key)
    payment_verifier = PaymentVerificationService(
        store,
        RapidOcrProvider(),
        load_payment_policy(settings.payment_policy_path),
    )
    api = WeComApiClient(settings.wecom_corp_id, settings.wecom_kf_secret)
    staff_service = StaffAppointmentService(
        store,
        PaymentCodes(settings.wechat_payment_code_path, settings.alipay_payment_code_path),
        WeComNativeHandoffGateway(
            api,
            store,
            settings.wecom_default_servicer_user_id,
        ),
    )
    sync_service = WeComSyncService(
        api,
        store,
        settings.wecom_media_path,
        payment_verifier,
    )
    callback_worker = WeComCallbackWorker(store, sync_service)
    dialogue = UnifiedDialogueEngine(
        DeepSeekChatModel(
            settings.deepseek_api_key,
            model=settings.deepseek_model,
            base_url=settings.deepseek_base_url,
            timeout_seconds=settings.model_timeout_seconds,
        ),
        store,
        CatalogKnowledgeProvider(
            MarkdownKnowledgeProvider(settings.knowledge_path),
            catalog_store,
        ),
    )
    message_pipeline = ReliableMessagePipeline(
        store,
        dialogue,
        WeComCustomerServiceGateway(
            api,
            store,
            staff_agent_id=settings.wecom_agent_id,
            staff_user_ids=settings.staff_wecom_user_ids,
            staff_base_url=settings.app_base_url,
            staff_signer=signer,
            catalog_images=catalog_store,
        ),
    )
    runtime = ServiceRuntime(
        callback_worker,
        message_pipeline,
        ActionExecutor(store),
    )
    receiver = WeComCallbackReceiver(
        WeComCallbackCrypto(
            settings.wecom_callback_token,
            settings.wecom_callback_aes_key,
            settings.wecom_corp_id,
        ),
        store,
    )
    server = create_wecom_callback_server(
        host if host is not None else settings.wecom_host,
        port if port is not None else settings.wecom_port,
        receiver,
        StaffTaskWebApp(
            staff_service,
            signer,
            wecom_client=api,
            corp_id=settings.wecom_corp_id,
            agent_id=settings.wecom_agent_id,
            staff_user_ids=settings.staff_wecom_user_ids,
            app_base_url=settings.app_base_url,
            catalog_store=catalog_store,
            session_signer=StaffSessionSigner(settings.staff_action_signing_key),
            password_verifier=StaffPasswordVerifier(settings.staff_web_accounts),
        ),
    )
    return server, runtime


def _run_worker(runtime: ServiceRuntime, stop: threading.Event) -> None:
    worker_id = f"wecom-{threading.get_native_id()}"
    while not stop.is_set():
        processed = runtime.process_cycle(worker_id)
        if not processed:
            stop.wait(0.5)


if __name__ == "__main__":
    main()
