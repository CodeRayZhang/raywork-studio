import argparse
import os
import secrets
from pathlib import Path

import uvicorn

from .api import create_app


def main():
    parser = argparse.ArgumentParser(description="RayWork Studio · 内容交付工作台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--mode", choices=["demo", "openai"], default=os.getenv("RAYWORK_MODE", "demo")
    )
    parser.add_argument("--data-dir", default=os.getenv("RAYWORK_DATA_DIR", ".state"))
    args = parser.parse_args()
    root = Path(args.data_dir)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    token = os.getenv("RAYWORK_ADMIN_TOKEN")
    if not token:
        token_file = root / "admin-token"
        if token_file.exists():
            token = token_file.read_text().strip()
        else:
            token = secrets.token_urlsafe(32)
            fd = os.open(token_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(token)
        print(f"\n管理令牌保存在 {token_file.resolve()}，在首页粘贴即可连接。")
    print(f"RayWork Studio: http://{args.host}:{args.port} · 模式 {args.mode}\n")
    uvicorn.run(
        create_app(root, token, args.mode), host=args.host, port=args.port, workers=1
    )


if __name__ == "__main__":
    main()
