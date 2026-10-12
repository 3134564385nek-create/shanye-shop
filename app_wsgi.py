"""通过标准网页服务接口承载山野装备商城。"""
from email.message import Message
from http import HTTPStatus
from io import BytesIO
from server import Handler


class WebRequest(Handler):
    """复用业务路由，通过宿主服务收发请求。"""

    def __init__(self, environ):
        self.command = environ['REQUEST_METHOD']
        self.path = environ.get('PATH_INFO', '/') or '/'
        if environ.get('QUERY_STRING'):
            self.path += '?' + environ['QUERY_STRING']
        self.headers = Message()
        for key, value in environ.items():
            if key.startswith('HTTP_'):
                self.headers[key[5:].replace('_', '-')] = value
        for key in ('CONTENT_TYPE', 'CONTENT_LENGTH'):
            if environ.get(key):
                self.headers[key.replace('_', '-')] = environ[key]
        if not self.headers.get('Host'):
            self.headers['Host'] = environ['SERVER_NAME'] + ':' + environ['SERVER_PORT']
        self.client_address = (environ.get('REMOTE_ADDR', 'unknown'), 0)
        self.rfile = environ['wsgi.input']
        self.wfile = BytesIO()
        self.status = 200
        self.response_headers = []
        self.secure = environ.get('wsgi.url_scheme') == 'https'

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, keyword, value):
        if keyword.lower() == 'set-cookie' and self.secure:
            value += '; Secure'
        self.response_headers.append((keyword, value))

    def end_headers(self):
        pass


def application(environ, start_response):
    request = WebRequest(environ)
    if request.command in ('GET', 'HEAD'):
        request.do_GET()
    elif request.command == 'POST':
        request.do_POST()
    else:
        request.send_header('Allow', 'GET, HEAD, POST')
        request.reply({'error': '不支持此请求方式'}, 405)
    status = f'{request.status} {HTTPStatus(request.status).phrase}'
    start_response(status, request.response_headers)
    return [b'' if request.command == 'HEAD' else request.wfile.getvalue()]
