"""Bounded public HTTP fetch with per-hop address checks and pinned connections."""
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlsplit, urljoin, quote
from .core import Problem, MAX_FILE


def destination(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError()
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if port not in (80, 443):
            raise ValueError()
        host = parsed.hostname.encode('idna').decode()
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (ValueError, UnicodeError, OSError):
        raise Problem('远程地址无效或 DNS 解析失败；仅允许公开 HTTP/HTTPS 的 80/443 端口') from None
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise Problem('不允许访问内网、回环、保留地址')
    return parsed, host, port, addresses[0]


def fetch(url):
    deadline = time.monotonic() + 40
    for _ in range(5):
        parsed, host, port, addr = destination(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Problem('远程下载超时', 504)
        sock = socket.socket(addr[0], addr[1], addr[2])
        conn = None
        try:
            sock.settimeout(min(10, remaining))
            sock.connect(addr[4])
            if parsed.scheme == 'https':
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.sock = sock
            target = quote(parsed.path or '/', safe="/%:@!$&'()*+,;=-._~")
            if parsed.query:
                target += '?' + quote(parsed.query, safe="%=&/:?@!$'()*+,;~-._")
            host_header = f'[{host}]' if ':' in host else host
            if port != (443 if parsed.scheme == 'https' else 80):
                host_header += ':' + str(port)
            conn.request('GET', target, headers={'Host':host_header, 'User-Agent':'WidgetHarbor/1.0', 'Accept-Encoding':'identity'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location:
                    raise Problem('远程跳转缺少地址')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise Problem('上游返回 HTTP %s' % response.status, 502)
            if response.getheader('Content-Encoding', 'identity') != 'identity':
                raise Problem('上游忽略 identity 编码要求')
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Problem('远程下载超时', 504)
                sock.settimeout(min(10, remaining))
                chunk = response.read1(min(65536, MAX_FILE + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE:
                    raise Problem('远程文件超过 5 MiB')
                chunks.append(chunk)
            return b''.join(chunks), url
        except (OSError, http.client.HTTPException) as exc:
            raise Problem('远程下载失败：' + type(exc).__name__, 502) from None
        finally:
            if conn:
                conn.close()
            sock.close()
    raise Problem('远程跳转次数过多')
