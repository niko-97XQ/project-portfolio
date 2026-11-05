from __future__ import annotations

import argparse
from pathlib import Path


REQUIRED_OLD_KEYS = (
    "DEEPSEEK_API_KEY",
    "WECOM_CORP_ID",
    "WECOM_KF_SECRET",
    "WECOM_CALLBACK_TOKEN",
    "WECOM_CALLBACK_AES_KEY",
    "WECOM_AGENT_ID",
    "OWNER_WECOM_USER_ID",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the private v2 production env file.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--signing-key", required=True)
    args = parser.parse_args()

    old = _load_env(Path(args.source))
    missing = [key for key in REQUIRED_OLD_KEYS if not old.get(key)]
    if missing:
        raise ValueError("old production environment is missing required fields")
    if len(args.signing_key) < 32:
        raise ValueError("staff action signing key is too short")

    values = {
        "DEEPSEEK_API_KEY": old["DEEPSEEK_API_KEY"],
        "DEEPSEEK_BASE_URL": old.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com",
        "DEEPSEEK_MODEL": (
            "deepseek-ai/DeepSeek-V4-Flash"
            if "siliconflow.cn" in (old.get("DEEPSEEK_BASE_URL") or "")
            else "deepseek-v4-flash"
        ),
        "MODEL_TIMEOUT_SECONDS": "20",
        "WECOM_CORP_ID": old["WECOM_CORP_ID"],
        "WECOM_KF_SECRET": old["WECOM_KF_SECRET"],
        "WECOM_CALLBACK_TOKEN": old["WECOM_CALLBACK_TOKEN"],
        "WECOM_CALLBACK_AES_KEY": old["WECOM_CALLBACK_AES_KEY"],
        "WECOM_AGENT_ID": old["WECOM_AGENT_ID"],
        "STAFF_WECOM_USER_IDS": old["OWNER_WECOM_USER_ID"],
        "APP_BASE_URL": old.get("APP_BASE_URL") or "https://api.forthatday-studio.com",
        "STAFF_ACTION_SIGNING_KEY": args.signing_key,
    }
    target = Path(args.target)
    target.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )
    target.chmod(0o600)
    print(f"Production configuration created; fields={len(values)}")
    return 0


def _load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" not in raw or raw.lstrip().startswith("#"):
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip().strip('"\'')
    return values


if __name__ == "__main__":
    raise SystemExit(main())
