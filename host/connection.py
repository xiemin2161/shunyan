"""Connection metadata and public-certificate-only onboarding; no private keys."""
from __future__ import annotations

import re
import ssl
import socket
import hashlib
import plistlib
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def client_device(user_agent: str, mobile_hint: str = "", device_hint: str = "") -> str:
    # Cosmetic hints never participate in pairing/authentication decisions.
    if device_hint in {"mobile", "desktop"}:
        return device_hint
    return "mobile" if mobile_hint == "?1" or re.search(r"Android|iPhone|iPad|iPod|Mobile", user_agent, re.I) else "desktop"


def public_certificate(path: str) -> bytes:
    raw = Path(path).read_bytes()
    if not raw or len(raw) > 65536 or b"PRIVATE KEY" in raw:
        raise ValueError("证书下载文件必须仅包含公开证书，不能包含私钥")
    if b"-----BEGIN CERTIFICATE-----" in raw:
        text = raw.decode("ascii").strip()
        if text.count("-----BEGIN CERTIFICATE-----") != 1 or not text.startswith("-----BEGIN CERTIFICATE-----") or not text.endswith("-----END CERTIFICATE-----"):
            raise ValueError("请单独提供一张需要手机信任的公开证书")
        raw = ssl.PEM_cert_to_DER_cert(text)
    # OpenSSL accepts a certificate followed by unrelated bytes. Reject any
    # trailing DER object (including a binary private key) before publication.
    if len(raw) < 2 or raw[0] != 0x30:
        raise ValueError("无效的公开证书")
    length = raw[1]
    header_length = 2
    if length & 0x80:
        count = length & 0x7f
        if not 1 <= count <= 4 or len(raw) < 2 + count:
            raise ValueError("无效的公开证书长度")
        length = int.from_bytes(raw[2:2+count], "big")
        header_length += count
    if header_length + length != len(raw):
        raise ValueError("下载文件只能包含单张公开证书，不能附带其他数据")
    # Parse X.509 without changing OS trust or loading a private key.
    ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(raw))
    return raw


def apple_certificate_profile(certificate: bytes) -> bytes:
    fingerprint = hashlib.sha256(certificate).hexdigest()
    identifier = "cc.xicon.shunyan.certificate." + fingerprint[:24]
    return plistlib.dumps({
        "PayloadType": "Configuration", "PayloadVersion": 1,
        "PayloadIdentifier": identifier,
        "PayloadUUID": str(uuid.uuid5(uuid.NAMESPACE_URL, identifier)),
        "PayloadDisplayName": "顺言 · 局域网证书",
        "PayloadDescription": "安装顺言电脑提供的公开证书，以便手机通过局域网 HTTPS 连接。安装后需手动开启证书完全信任。",
        "PayloadRemovalDisallowed": False,
        "PayloadContent": [{
            "PayloadType": "com.apple.security.root", "PayloadVersion": 1,
            "PayloadIdentifier": identifier + ".root",
            "PayloadUUID": str(uuid.uuid5(uuid.NAMESPACE_URL, identifier + ".root")),
            "PayloadDisplayName": "顺言局域网公开证书",
            "PayloadCertificateFileName": "shunyan-phone-root.cer",
            "PayloadContent": certificate,
        }],
    }, fmt=plistlib.FMT_XML, sort_keys=False)


def send_certificate(handler: BaseHTTPRequestHandler, certificate: bytes | None, *, apple_profile: bool = False) -> None:
    if not certificate:
        handler.send_error(404, "Certificate download unavailable")
        return
    body = apple_certificate_profile(certificate) if apple_profile else certificate
    handler.send_response(200)
    handler.send_header("Content-Type", "application/x-apple-aspen-config" if apple_profile else "application/pkix-cert")
    handler.send_header("Content-Disposition", 'inline; filename="shunyan-certificate.mobileconfig"' if apple_profile else 'attachment; filename="shunyan-phone-root.cer"')
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body)


def certificate_bootstrap(address: tuple[str, int], certificate: bytes, connect_url: str) -> ThreadingHTTPServer:
    class CertificateHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def do_GET(self) -> None:
            if self.path == "/certificate.cer":
                send_certificate(self, certificate)
            elif self.path == "/certificate.mobileconfig":
                send_certificate(self, certificate, apple_profile=True)
            elif self.path == "/":
                body = (f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                        f'<title>顺言证书安装</title><h1>连接顺言</h1><p>先安装并信任这台电脑的证书，再打开控制页面。</p>'
                        f'<p><a href="/certificate.mobileconfig">苹果手机：下载证书描述文件</a></p><p><a href="/certificate.cer">安卓及其他设备：下载公开证书</a></p>'
                        f'<p>iPhone/iPad：请在 Safari 中打开，点击下载并允许下载描述文件；然后打开“设置 → 已下载描述文件”安装（也可在“通用 → VPN 与设备管理”查找）。最后到“通用 → 关于本机 → 证书信任设置”开启完全信任。iOS 不允许网页自动安装或开启信任。</p>'
                        f'<p>安卓：在系统设置中搜索“安装证书”，选择 CA 证书；具体入口因系统而异。</p>'
                        f'<p>请仅安装你确认由自己的电脑提供的证书。</p><p><a href="{connect_url}">打开顺言连接页面</a></p></html>').encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

    class CertificateServer(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
    server = CertificateServer(address, CertificateHandler)
    server.daemon_threads = True
    return server
