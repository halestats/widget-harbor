import json
import socket
import unittest
from unittest.mock import patch
from hub.core import Problem
from hub.network import destination, resolve_fake_ip


def addr(ip):
    return (socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))


class FakeIPTests(unittest.TestCase):
    def test_fallback_and_custom_port(self):
        with patch('hub.network.socket.getaddrinfo', return_value=[addr('198.18.5.61')]), patch('hub.network.resolve_fake_ip', return_value=[addr('104.21.70.243')]) as resolve:
            result = destination('https://example.com:5090/file.json')
            resolve.assert_called_once_with('example.com', 5090)
            self.assertEqual(result[3][4][0], '104.21.70.243')

    def test_private_and_literal_not_bypassed(self):
        for url, ips in [('https://198.18.5.61/', ['198.18.5.61']), ('https://example.com/', ['198.18.5.61', '127.0.0.1']), ('https://example.com/', ['10.0.0.1'])]:
            with self.subTest(url=url, ips=ips), patch('hub.network.socket.getaddrinfo', return_value=list(map(addr, ips))), patch('hub.network.resolve_fake_ip') as resolve:
                with self.assertRaises(Problem):
                    destination(url)
                resolve.assert_not_called()

    def test_public_dns_unchanged(self):
        with patch('hub.network.socket.getaddrinfo', return_value=[addr('8.8.8.8')]), patch('hub.network.resolve_fake_ip') as resolve:
            destination('https://example.com/')
            resolve.assert_not_called()

    def test_doh_response_validation(self):
        for ips, valid in [(['8.8.8.8'], True), (['8.8.8.8', '127.0.0.1'], False), (['198.18.5.61'], False), ([], False)]:
            with self.subTest(ips=ips), patch('hub.network.http.client.HTTPSConnection') as connection:
                response = connection.return_value.getresponse.return_value
                response.status = 200
                response.read.return_value = json.dumps({'Status': 0, 'Answer': [{'type': 1, 'data': ip} for ip in ips]}).encode()
                if valid:
                    self.assertEqual(resolve_fake_ip('example.com', 5090)[0][4], ('8.8.8.8', 5090))
                else:
                    with self.assertRaises(Problem):
                        resolve_fake_ip('example.com', 5090)
                connection.return_value.close.assert_called_once()

    def test_doh_failure_closed(self):
        for status, body in [(302, b'{}'), (200, b'x'*65537), (200, b'not json'), (200, b'{"Status":3}'), (200, b'[]')]:
            with self.subTest(status=status, body=body[:20]), patch('hub.network.http.client.HTTPSConnection') as connection:
                response = connection.return_value.getresponse.return_value
                response.status, response.read.return_value = status, body
                with self.assertRaises(Problem):
                    resolve_fake_ip('example.com', 443)
