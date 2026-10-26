"""Let the user enter the engineering sample's API key without echoing it."""

import getpass
import os
from pathlib import Path
import sys
import tempfile


KEY_PATH = Path(__file__).resolve().parents[1] / ".local/renewal/credentials/deepseek.key"


def store_key(key: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".key-", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(key)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    if not sys.stdin.isatty():
        print("请在自己的交互式 Ubuntu 终端运行；程序只接受不回显输入。", file=sys.stderr)
        return 1
    try:
        key = getpass.getpass("DeepSeek API Key（输入不回显）: ").strip()
        if not key or any(character.isspace() for character in key):
            print("未保存：请输入有效的 API Key。", file=sys.stderr)
            return 1
        store_key(key, KEY_PATH)
    except (EOFError, KeyboardInterrupt):
        print("已取消，未保存新的凭据。", file=sys.stderr)
        return 1
    except OSError:
        print("未能保存凭据，请检查本机目录权限。", file=sys.stderr)
        return 1
    print("凭据已保存；仅调试程序会使用，不回显内容。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
