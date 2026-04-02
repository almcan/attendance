#!/usr/bin/env python3
"""
管理者ログイン用ハッシュ生成ツール
====================================
ADMIN_USERNAME_HASH と ADMIN_PASSWORD_HASH を生成して .env に貼り付けてください。

使い方:
  .venv/bin/python generate_admin_hash.py
"""
import hashlib
import getpass
import secrets


def hash_value(value: str) -> str:
    """SHA-256 でハッシュ化する。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main():
    print("=" * 50)
    print("  管理者ハッシュ生成ツール")
    print("=" * 50)
    print()

    username = input("管理者ユーザー名: ").strip()
    if not username:
        print("エラー: ユーザー名を入力してください。")
        return

    password = getpass.getpass("管理者パスワード: ")
    if not password:
        print("エラー: パスワードを入力してください。")
        return

    password_confirm = getpass.getpass("パスワード（確認）: ")
    if password != password_confirm:
        print("エラー: パスワードが一致しません。")
        return

    username_hash = hash_value(username)
    password_hash = hash_value(password)
    secret_key = secrets.token_hex(32)

    print()
    print("以下を .env ファイルに追加してください:")
    print("-" * 50)
    print(f"ADMIN_USERNAME_HASH={username_hash}")
    print(f"ADMIN_PASSWORD_HASH={password_hash}")
    print(f"FLASK_SECRET_KEY={secret_key}")
    print("-" * 50)
    print()
    print("✅ 完了！設定後にサービスを再起動してください。")
    print("   sudo systemctl restart attendance.service")


if __name__ == "__main__":
    main()
