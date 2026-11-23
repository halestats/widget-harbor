"""Run inside the app image with Docker's production 16 MiB /tmp mount."""

import base64
import http.client
import json
from tempfile import TemporaryDirectory
from threading import Thread

from hub.core import prepare_module
from hub.server import HubServer


def request(port, path, *, body=None, cookie=''):
    headers = {'Content-Type': 'application/json', 'X-Hub-Request': '1', 'Cookie': cookie}
    client = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        client.request('GET' if body is None else 'POST', path,
                       None if body is None else json.dumps(body), headers)
        response = client.getresponse()
        return response.status, response.getheader('Set-Cookie'), response.read()
    finally:
        client.close()


def main():
    password = 'ci-console-fixture-only'
    module_size = 4 * 1024 * 1024
    data = base64.b64encode(b'x' * module_size).decode()
    with TemporaryDirectory(dir='/data') as directory:
        app = HubServer(('127.0.0.1', 0), directory, password, 'http://127.0.0.1:8765')
        with app.store.connect() as db:
            for index in range(7):
                module = prepare_module({'filename': f'fixture-{index}.js', 'data': data})
                module['updated_at'] = index
                app.store.save_module(db, module)

        worker = Thread(target=app.serve_forever, daemon=True)
        worker.start()
        try:
            port = app.server_port
            assert request(port, '/healthz')[0] == 200
            status, token, body = request(port, '/api/login', body={'password': password})
            assert status == 200, (status, body)
            cookie = token.split(';', 1)[0]
            status, _, body = request(port, '/api/state', cookie=cookie)
            assert status == 200, (status, body)
            modules = json.loads(body)['modules']
            assert [module['filename'] for module in modules] == [f'fixture-{index}.js' for index in reversed(range(7))]
            assert all('content' not in module and module['size'] == module_size for module in modules)
            assert request(port, '/api/profile', cookie=cookie)[0] == 200
            print('Login and console loaded with 28 MiB of files and 16 MiB of temporary storage.')
        finally:
            app.shutdown()
            app.server_close()
            worker.join()


if __name__ == '__main__':
    main()
