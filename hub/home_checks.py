"""Bounded static checks for Rex home packages."""

from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

from .core import Problem

HTML_LIMIT = 32768
DETAIL_LIMIT = 200
ITEM_FIELDS = ('style', 'count', 'gridSize', 'id', 'title', 'subtitle', 'iconUrl',
               'functionId', 'params', 'widgetBundleId', 'widgetId', 'widgetURL', 'name')


def local_name(value):
    return bool(value and value not in ('.', '..') and
                not any(c in value for c in '/\\:%?#') and
                all(ord(c) >= 32 and ord(c) != 127 and not 0xD800 <= ord(c) <= 0xDFFF for c in value))


class TemplateTags(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.declarations = []
        self.external = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'meta' and (attributes.get('name') or '').strip().casefold() == 'rex-widget':
            if len(self.declarations) < 2:
                self.declarations.append(attributes.get('content') or '')
        self.external |= any((attributes.get(key) or '').strip().lower().startswith(('http:', 'https:', '//'))
                             for key in ('src', 'href', 'poster'))


def check_template(raw, size, *, strict=False):
    html = raw.decode('utf-8-sig', errors='replace')
    parser = TemplateTags()
    issues = []
    shape = None
    values = {}
    try:
        parser.feed(html)
    except (AssertionError, ValueError):
        issues.append('HTML 标记不完整或无法识别')
    if size > HTML_LIMIT:
        issues.append('HTML 超过 32,768 字节，检查范围为文件开头')
    if len(parser.declarations) != 1:
        issues.append('需要且只能有一个 rex-widget 声明')
    else:
        values = dict((key.strip().lower(), value.strip()) for field in parser.declarations[0].split(';')
                      for key, separator, value in (field.partition('='),) if separator)
        shape = values.get('type')
        if shape not in ('card', 'full-width', 'rail'):
            issues.append('rex-widget 的 type 无法识别')
        elif shape == 'card' and any(values.get(key) not in ('x1', 'x2', 'x3', 'x4') for key in ('width', 'height')):
            issues.append('方卡的宽高应为 x1 到 x4')
        elif shape in ('full-width', 'rail'):
            height = values.get('height', '')
            minimum, maximum = (96, 960) if shape == 'full-width' else (50, 2048)
            if not re.fullmatch('[0-9]{1,4}', height) or not minimum <= int(height) <= maximum:
                issues.append(f'此类型的卡片高度应为 {minimum} 到 {maximum} 的整数')
            if shape == 'rail':
                ratio = re.fullmatch(r'([0-9]{1,5}(?:\.[0-9]{1,5})?):([0-9]{1,5}(?:\.[0-9]{1,5})?)',
                                     values.get('item-ratio', ''))
                if not ratio or not all(map(float, ratio.groups())):
                    issues.append('横滑卡片缺少有效的 item-ratio')
                else:
                    width, height = map(float, ratio.groups())
                    if width > 3 * height or height > 3 * width:
                        issues.append('横滑卡片的 item-ratio 应在 1:3 到 3:1 之间')
    if strict and issues:
        raise Problem('HTML 卡片声明无效：' + '；'.join(issues))
    if parser.external:
        issues.append('包含外部资源地址')
    if re.search(r'\b(?:fetch\s*\(|XMLHttpRequest\b|WebSocket\s*\()', html):
        issues.append('发现可能发起网络请求的代码')
    return values, issues


def check_item(archive, names, item, position, templates, url):
    missing = [field for field in ITEM_FIELDS if field not in item]
    issues = ['布局缺少字段：' + '、'.join(missing)] if missing else []
    card = None
    if 'htmlTemplate' in item and item['htmlTemplate'] is not None:
        template = item['htmlTemplate']
        if not isinstance(template, str) or not template.strip():
            raise Problem(f'{position} 的 HTML 模板无效')
        if template.lstrip('\ufeff \t\r\n').startswith('<'):
            raw = template.encode('utf-8', errors='replace')
            size = len(raw)
            values, template_issues = check_template(raw[:HTML_LIMIT], size)
            card = dict(position=position, source='内联', size=size)
        else:
            if not local_name(template) or not template.lower().endswith(('.html', '.htm')):
                raise Problem(f'{position} 的 HTML 模板文件名不安全')
            name = 'rex-html/' + template
            if name not in names:
                raise Problem(f'{position} 找不到 HTML 模板 {name}')
            if name not in templates:
                size = archive.getinfo(name).file_size
                with archive.open(name) as stream:
                    values, template_issues = check_template(stream.read(HTML_LIMIT), size)
                    templates[name] = size, values, template_issues
            size, values, template_issues = templates[name]
            card = dict(position=position, source='包内文件', filename=template, size=size)
        issues.extend(template_issues)
        shape = values.get('type')
        if shape in ('card', 'full-width', 'rail') and ('style' in item or 'gridSize' in item):
            grid = item.get('gridSize')
            style = 'fullWidthPoster' if shape == 'rail' else 'cover'
            if item.get('style') != style or not isinstance(grid, list) or not grid or (grid[0] == -1) != (shape != 'card'):
                issues.append('卡片样式或网格尺寸与 HTML 声明不一致')
    local = url.startswith('file://') and not url.startswith('file:///')
    script = url.removeprefix('file:///') if url.startswith('file:///') else ''
    if local:
        issues.append('依赖原设备的本机模块')
    elif url.startswith('file:///') and (not local_name(script) or 'rex-scripts/' + script not in names):
        issues.append('找不到引用的内置脚本')
    return card, issues, local, script


def reference_host(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme in ('https', 'http'):
            return parsed.hostname
    except ValueError:
        pass
    return None
