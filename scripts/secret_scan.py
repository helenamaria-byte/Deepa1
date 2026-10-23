"""Fails if anything that looks like a credential is in the files git tracks.

    python scripts/secret_scan.py
"""
import re
import subprocess
import sys

PATTERNS = {
    "Neon API key": r"napi_[a-z0-9]{40,}",
    "Vercel token": r"vcp_[A-Za-z0-9]{30,}",
    "Resend key": r"re_[A-Za-z0-9]{8,}_[A-Za-z0-9]{12,}",
    "Anthropic key": r"sk-ant-[A-Za-z0-9_-]{20,}",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{30,}",
    "Postgres URL with password": r"postgres(?:ql)?://[^:\s/]+:[^@\s]{6,}@",
    "AWS key": r"AKIA[0-9A-Z]{16}",
    "Zoho token or code": r"1000\.[0-9a-f]{32}\.[0-9a-f]{32}",
    "Zoho client secret": r"\b[0-9a-f]{42}\b",
    "Private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
}


def main() -> int:
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                           capture_output=True, text=True, check=True).stdout.split()
    hits = []
    for path in files:
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for name, pattern in PATTERNS.items():
            if re.search(pattern, text):
                hits.append(f"{path}: possible {name}")
    if path_hits := [f for f in files if f == ".env" or f.endswith("/.env")]:
        hits += [f"{p}: .env must not be committed" for p in path_hits]
    for h in hits:
        print(h)
    print(f"Secret scan: {len(files)} files checked, {len(hits)} problems")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
