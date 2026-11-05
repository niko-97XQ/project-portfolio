from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_DIRECTORIES = {"data", "private", "backups", "node_modules", ".venv"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".pfx", ".zip", ".mp4"}
CREDENTIAL = re.compile(
    r"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----"
    r"|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{40,}"
    r"|sk-[A-Za-z0-9]{24,}"
)
PRODUCTION = re.compile(
    r"/home/(?:ubuntu|root)/|[A-Z]:[\\/]Users[\\/]"
    r"|(?<!\d)1[3-9]\d{9}(?!\d)"
    r"|(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)"
)
ALLOWED_HOSTS = {"niko-97xq.github.io", "github.com", "api.deepseek.com", "qyapi.weixin.qq.com", "example.test"}


def main() -> None:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True,
    )
    names = [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
    if not names:
        raise SystemExit("No tracked files; stage intended public files before checking.")
    findings = []
    for name in names:
        path = ROOT / name
        if not path.is_file():
            findings.append(f"{name}: missing tracked file")
            continue
        if (
            set(path.relative_to(ROOT).parts) & FORBIDDEN_DIRECTORIES
            or path.suffix.lower() in FORBIDDEN_SUFFIXES
            or (path.name.startswith(".env") and path.name != ".env.example")
        ):
            findings.append(f"{name}: private/runtime file")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(f"{name}: binary file requires separate rights/privacy review")
            continue
        if CREDENTIAL.search(text):
            findings.append(f"{name}: credential pattern")
        if PRODUCTION.search(text):
            findings.append(f"{name}: production detail or personal data pattern")
        for host in re.findall(r"https?://([A-Za-z0-9.-]+)", text):
            if host not in ALLOWED_HOSTS:
                findings.append(f"{name}: non-allowlisted URL host")
    if findings:
        raise SystemExit("\n".join(findings))
    print(f"Public-content checks passed: {len(names)} tracked files. Manual review is still required.")


if __name__ == "__main__":
    main()
