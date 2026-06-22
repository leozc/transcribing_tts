#!/usr/bin/env python
"""Bilibili QR-code web login -> save session cookies for yt-dlp.

Generates a login QR (scan with the Bilibili mobile app), polls until confirmed,
and writes a Netscape cookies.txt (SESSDATA, bili_jct, DedeUserID, ...). A
logged-in SESSDATA bypasses Bilibili's HTTP 412 risk-control on datacenter IPs.

    python scripts/bili_login.py [out_cookies.txt]

Cookies are SENSITIVE (account auth) — written 0600 to a gitignored path.
"""
import http.cookiejar
import os
import sys
import time

import qrcode
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
QR_PNG = "/tmp/bili_qr.png"
OUT = sys.argv[1] if len(sys.argv) > 1 else "data/bili_cookies.txt"


def main():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
    # warm up buvid cookies
    s.get("https://www.bilibili.com/", timeout=15)

    g = s.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
              timeout=15).json()["data"]
    url, key = g["url"], g["qrcode_key"]

    qrcode.make(url).save(QR_PNG)
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.print_ascii(out=sys.stdout)
    print(f"\nQR saved to {QR_PNG}", flush=True)
    print("Scan with the Bilibili app (扫一扫) and confirm login.", flush=True)

    poll = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
    for _ in range(150):  # ~5 min
        d = s.get(poll, params={"qrcode_key": key}, timeout=15).json()["data"]
        code = d["code"]
        if code == 0:
            print("\nLOGIN OK — confirmed.", flush=True)
            break
        if code == 86038:
            print("\nQR EXPIRED — rerun.", flush=True)
            sys.exit(1)
        # 86101 = waiting for scan, 86090 = scanned, awaiting confirm
        print(f"  waiting... (code {code})", flush=True)
        time.sleep(2)
    else:
        print("\nTIMED OUT waiting for scan.", flush=True)
        sys.exit(1)

    names = {c.name for c in s.cookies}
    if "SESSDATA" not in names:
        print(f"WARN: SESSDATA not in cookies ({names})", flush=True)
        sys.exit(1)

    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    jar = http.cookiejar.MozillaCookieJar(OUT)
    for c in s.cookies:
        jar.set_cookie(c)
    jar.save(ignore_discard=True, ignore_expires=True)
    os.chmod(OUT, 0o600)
    print(f"saved {len([*s.cookies])} cookies -> {OUT}  (SESSDATA + {sorted(names - {'SESSDATA'})})", flush=True)


if __name__ == "__main__":
    main()
