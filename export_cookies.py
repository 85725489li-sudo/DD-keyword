"""
本地运行此脚本，从 Chrome 导出点点数据的 Cookie
运行前请确保已在 Chrome 中登录 app.diandian.com

用法：python export_cookies.py
输出：复制打印的 JSON 字符串，粘贴到 Render 环境变量 DD_COOKIES
"""
import json
import sqlite3
import os
import shutil
import tempfile

def export_diandian_cookies():
    # Chrome Cookie 路径（Windows）
    chrome_cookie_path = os.path.expandvars(
        r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Network\Cookies"
    )

    if not os.path.exists(chrome_cookie_path):
        print("未找到 Chrome Cookie 文件")
        return

    # 复制文件（Chrome 可能锁定）
    tmp = tempfile.mktemp(suffix=".db")
    shutil.copy2(chrome_cookie_path, tmp)

    try:
        conn = sqlite3.connect(tmp)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, value, host_key, path, expires_utc, is_secure, is_httponly, encrypted_value "
            "FROM cookies WHERE host_key LIKE '%diandian.com'"
        ).fetchall()
        conn.close()

        cookies = []
        for row in rows:
            val = row["value"]
            if not val and row["encrypted_value"]:
                # 尝试解密（需要 pycryptodome）
                try:
                    import win32crypt
                    val = win32crypt.CryptUnprotectData(row["encrypted_value"], None, None, None, 0)[1].decode('utf-8')
                except Exception:
                    try:
                        import base64
                        from Crypto.Cipher import AES
                        local_state_path = os.path.expandvars(
                            r"%LOCALAPPDATA%\Google\Chrome\User Data\Local State"
                        )
                        with open(local_state_path) as f:
                            local_state = json.load(f)
                        encrypted_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])[5:]
                        import win32crypt
                        key = win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]
                        enc = row["encrypted_value"]
                        iv = enc[3:15]
                        payload = enc[15:]
                        cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
                        val = cipher.decrypt(payload)[:-16].decode('utf-8')
                    except Exception:
                        val = ""

            if val:
                cookies.append({
                    "name": row["name"],
                    "value": val,
                    "domain": row["host_key"],
                    "path": row["path"],
                    "secure": bool(row["is_secure"]),
                    "httpOnly": bool(row["is_httponly"]),
                    "sameSite": "Lax",
                })

        if cookies:
            print(f"\n✅ 找到 {len(cookies)} 个 Cookie\n")
            print("复制以下 JSON，粘贴到 Render 环境变量 DD_COOKIES：")
            print("=" * 60)
            print(json.dumps(cookies, ensure_ascii=False))
            print("=" * 60)
        else:
            print("⚠️ 未找到有效 Cookie，请确认已在 Chrome 登录 app.diandian.com")

    finally:
        os.unlink(tmp)


if __name__ == "__main__":
    export_diandian_cookies()
