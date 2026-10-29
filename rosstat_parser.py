"""Discovery, immutable source snapshots and explicit monthly Excel extraction."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# These are landing pages, not assumed permanent file URLs. Editable in the notebook.
PAGES = {
    'prices': 'https://rosstat.gov.ru/statistics/price',
    'industry': 'https://rosstat.gov.ru/enterprise_industrial',
    'retail': 'https://rosstat.gov.ru/statistics/roznichnayatorgovlya',
    'labour': 'https://rosstat.gov.ru/labor_market_employment_salaries',
    'services': 'https://rosstat.gov.ru/uslugi',
    'accounts': 'https://rosstat.gov.ru/statistics/accounts',
}
MONTHS = ('январь февраль март апрель май июнь июль август сентябрь октябрь ноябрь декабрь').split()
MONTH_FORMS = ('января февраля марта апреля мая июня июля августа сентября октября ноября декабря').split()
MISSING = {'', '-', '–', '—', '...', '…', 'н/д', 'н.д.', 'nan', 'none'}
CATALOG_COLUMNS = ['source_id', 'topic', 'title', 'display_title', 'context', 'url', 'page_url',
                   'file_name', 'section', 'group_title', 'period_label', 'page_date', 'frequency_hint']


def text(value):
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ''
    return re.sub(r'\s+', ' ', str(value).replace('\xa0', ' ').replace('\u202f', ' ')).strip()


def number(value):
    """Keep missing values; reject footnotes/unrecognised tokens instead of guessing."""
    value = text(value).lower()
    if value in MISSING:
        return float('nan')
    value = value.replace(' ', '').replace(',', '.').replace('−', '-')
    if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', value):
        raise ValueError(f'Нераспознанное число {value!r}; проверьте сноски и единицу измерения')
    return float(value)


def month_number(value):
    label = text(value).lower().rstrip('.')
    for i, (a, b) in enumerate(zip(MONTHS, MONTH_FORMS), 1):
        if label in (a, b):
            return i
    return None  # In particular, never accept "январь-март" as March.


def year_number(value):
    label = text(value)
    # Rosstat marks years as 2023(2), 20231) or 2026* in real workbooks.
    if re.fullmatch(r'(?:19|20)\d{2}(?:\.0|\s*г\.?)?(?:\s*(?:\(\d+\)|\d+\)|\*+))?', label):
        return int(label[:4])
    return None


def quarter_number(value):
    label = text(value).lower()
    if re.fullmatch(r'[1-4]\.0', label):
        label = label[0]
    match = re.fullmatch(r'(iv|iii|ii|i|[1-4])\s*(?:квартал|кв\.?)?', label)
    if not match:
        return None
    return {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4}.get(match[1],
             int(match[1]) if match[1].isdigit() else None)


def session(retries=3):
    s = requests.Session()  # Preserve inherited proxy and TLS trust settings.
    # This trust bundle is scoped to Rosstat; the system trust store stays intact.
    ca_bundle = Path(__file__).resolve().parent / 'certificates' / 'rosstat_ca_bundle.pem'
    if not (os.environ.get('REQUESTS_CA_BUNDLE') or os.environ.get('CURL_CA_BUNDLE')):
        s.verify = str(ca_bundle)
    s.headers['User-Agent'] = 'rosstat-macro-research/0.1 (low-rate public statistics downloader)'
    retry = Retry(total=retries, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=['GET'], respect_retry_after_header=True, raise_on_status=False)
    s.mount('https://', HTTPAdapter(max_retries=retry))
    return s


def official_url(url):
    p = urlparse(url)
    if p.scheme != 'https' or p.hostname not in {'rosstat.gov.ru', 'www.rosstat.gov.ru'}:
        raise ValueError(f'Разрешены только HTTPS-ссылки rosstat.gov.ru: {url}')
    return url


class ProxyTLSFailure(requests.exceptions.SSLError):
    """The proxy, rather than the local client, rejected the upstream certificate."""


def fetch(s, url):
    official_url(url)
    r = s.get(url, timeout=(15, 60), allow_redirects=False)
    # Validate each redirect before following it (no third-party downloads).
    for _ in range(5):
        if r.status_code not in (301, 302, 303, 307, 308):
            break
        url = official_url(urljoin(url, r.headers['Location']))
        r = s.get(url, timeout=(15, 60), allow_redirects=False)
    if r.status_code == 503 and b'CERTIFICATE_VERIFY_FAILED' in r.content:
        raise ProxyTLSFailure('Прокси не смог проверить сертификат Росстата '
                              '(HTTP 503 / CERTIFICATE_VERIFY_FAILED).')
    r.raise_for_status()
    if r.status_code != 200:
        raise ValueError(f'Ожидался HTTP 200, получен {r.status_code}')
    return r


def links_from_html(html, page_url, topic):
    soup = BeautifulSoup(html, 'html.parser')
    rows = []
    for a in soup.select('a[href]'):
        url = urljoin(page_url, a['href'])
        path = unquote(urlparse(url).path)
        if not re.search(r'\.xlsx?$', path, re.I):
            continue
        try:
            official_url(url)
        except ValueError:
            continue
        # The title of a media-bank link can be in the surrounding card.
        parent = (a.find_parent(class_='document-list__item')
                  or a.find_parent(class_='document') or a.parent)
        heading = parent.select_one('.document-list__item-title')
        title = text((heading or a).get_text(' ', strip=True))
        info_tag = parent.select_one('.document-list__item-info')
        info = text(info_tag.get_text(' ', strip=True)) if info_tag else ''
        sections, periods, groups = [], [], []
        for ancestor in reversed(list(a.parents)):
            classes = ancestor.get('class', [])
            if 'toggle-card' in classes:
                header = ancestor.find('div', class_='toggle-card__header', recursive=False)
                if header:
                    label = header.select_one('.toggle-card__title')
                    if label:
                        sections.append(text(label.get_text(' ', strip=True)))
            if 'list-tree__item' in classes:
                right = ancestor.find('div', class_='list-tree__item-right', recursive=False)
                header = right.find('div', class_='list-tree__item-header', recursive=False) if right else None
                if header:
                    periods.append(text(header.get_text(' ', strip=True)))
            if 'document-list__item--list-tree' in classes:
                intro = ancestor.find_previous_sibling('div', class_='content')
                if intro:
                    groups.append(text(intro.get_text(' ', strip=True)))
        section, group, period = ' / '.join(sections), ' / '.join(dict.fromkeys(groups)), ' / '.join(periods)
        description = ' '.join([title, group]).lower()
        frequency = ('monthly' if re.search(r'месяц|месяцы', description) else
                     'quarterly' if 'квартал' in description else
                     'weekly' if 'еженедельн' in description else
                     'annual' if re.search(r'\bгоды\b|годовые', description) else 'unknown')
        date = re.search(r'(\d{2}\.\d{2}\.\d{4})$', info)
        label = ' / '.join(filter(None, [section, group, period, title])) if month_number(title) else title
        rows.append({'source_id': hashlib.sha256(url.encode()).hexdigest()[:16],
                     'topic': topic, 'title': title, 'display_title': label,
                     'context': ' | '.join(filter(None, [section, group, period, text(parent.get_text(' ', strip=True))]))[:2000],
                     'url': url, 'page_url': page_url, 'file_name': path.rsplit('/', 1)[-1],
                     'section': section, 'group_title': group, 'period_label': period,
                     'page_date': date.group(1) if date else '', 'frequency_hint': frequency})
    return rows


def catalog_from_saved_html(path, page_url, topic='saved_page'):
    """Read uploaded HTML as data only: no scripts or embedded requests execute."""
    official_url(page_url)
    # BeautifulSoup detects the document encoding from the original bytes.
    rows = links_from_html(Path(path).read_bytes(), page_url, topic)
    return (pd.DataFrame(rows, columns=CATALOG_COLUMNS)
            .drop_duplicates(['topic', 'url']).reset_index(drop=True))


def discover(s, pages=PAGES, delay=1.0):
    rows, errors = [], []
    for topic, url in pages.items():
        try:
            r = fetch(s, url)
            r.encoding = 'utf-8'
            found = links_from_html(r.text, url, topic)
            if not found:
                raise ValueError('Excel-ссылки не найдены: проверьте страницу/динамическую загрузку')
            rows.extend(found)
        except (requests.RequestException, ValueError) as exc:
            errors.append({'topic': topic, 'page_url': url, 'error': str(exc)})
        time.sleep(delay)
    return (pd.DataFrame(rows, columns=CATALOG_COLUMNS)
            .drop_duplicates(['topic', 'url']).reset_index(drop=True), pd.DataFrame(errors))


def snapshot(s, url, root, refresh=False):
    """Store content-addressed files and a receipt; cached reads verify integrity."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    official_url(url)
    key = hashlib.sha256(url.encode()).hexdigest()
    receipt = root / f'{key}.json'
    if receipt.exists() and not refresh:
        meta = json.loads(receipt.read_text())
        path = root / meta['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != meta['sha256']:
            raise ValueError(f'Поврежден кеш {path}; загрузите заново с refresh=True')
        return path, meta
    r = fetch(s, url)
    payload = r.content
    # The server may return an HTML anti-bot/login page with status 200.
    if payload.startswith(b'PK\x03\x04'):
        suffix = '.xlsx'
    elif payload.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):
        suffix = '.xls'
    else:
        raise ValueError('Ответ не является XLS/XLSX (возможно, HTML-заглушка)')
    digest = hashlib.sha256(payload).hexdigest()
    path = root / (digest + suffix)
    path.write_bytes(payload)
    meta = {'url': url, 'final_url': r.url, 'file': path.name, 'sha256': digest,
            'retrieved_at': datetime.now(timezone.utc).isoformat(),
            'last_modified': r.headers.get('Last-Modified'), 'bytes': len(payload),
            'acquisition': 'https_download'}
    receipt.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return path, meta


def extract_monthly(frame, spec):
    """Explicit 0-based coordinates. Never infer which economic series a sheet means.

    year_columns: {Excel column index: year}, month_col, rows (individual months).
    month_columns: {Excel column index: month}, year_col, rows (individual years).
    long: date_col (real dates or ISO YYYY-MM), value_col, rows.
    """
    records = []
    rows = spec['rows']
    if not rows or len(rows) != len(set(rows)):
        raise ValueError('rows должен содержать неповторяющиеся номера строк')
    layout = spec['layout']
    for row in rows:
        if layout == 'year_columns':
            month = month_number(frame.iat[row, spec['month_col']])
            if month is None:
                raise ValueError(f'Строка {row}: ожидался отдельный месяц')
            for col, year in spec['year_columns'].items():
                records.append((pd.Timestamp(year=int(year), month=month, day=1),
                                number(frame.iat[row, int(col)])))
        elif layout == 'month_columns':
            year = year_number(frame.iat[row, spec['year_col']])
            if year is None:
                raise ValueError(f'Строка {row}: ожидался год')
            for col, month in spec['month_columns'].items():
                records.append((pd.Timestamp(year=year, month=int(month), day=1),
                                number(frame.iat[row, int(col)])))
        elif layout == 'long':
            raw = frame.iat[row, spec['date_col']]
            if not isinstance(raw, (datetime, pd.Timestamp)) and not re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?', text(raw)):
                raise ValueError(f'Строка {row}: нужна дата Excel или ISO, получено {raw!r}')
            date = pd.Timestamp(raw).to_period('M').to_timestamp()
            records.append((date, number(frame.iat[row, spec['value_col']])))
        else:
            raise ValueError(f'Неизвестная раскладка: {layout}')
    result = pd.DataFrame(records, columns=['period', 'value']).sort_values('period')
    if result.empty or result['value'].notna().sum() == 0:
        raise ValueError('Не извлечено ни одного значения')
    if result['period'].duplicated().any():
        raise ValueError('Повторяющиеся месяцы: проверьте строки/измерения таблицы')
    if any(not math.isfinite(v) for v in result['value'].dropna()):
        raise ValueError('Бесконечные значения')
    return result.reset_index(drop=True)


def parse_source(path, meta, spec):
    for key in ('indicator', 'unit', 'basis', 'territory', 'sheet', 'expected_text'):
        if not spec.get(key):
            raise ValueError(f'Заполните {key}')
    if spec['territory'] != 'Российская Федерация':
        raise ValueError('В этой витрине используются только итоги по РФ')
    frame = pd.read_excel(path, sheet_name=spec['sheet'], header=None)
    all_text = ' '.join(text(v).lower() for v in frame.to_numpy().ravel())
    if not all(text(token).lower() in all_text for token in spec['expected_text']):
        raise ValueError('Не совпали контрольные заголовки: структура/содержание файла изменились')
    for check in spec.get('expected_cells', []):
        if text(frame.iat[check['row'], check['column']]) != check['text']:
            raise ValueError(f"Изменилась контрольная ячейка {check['row']}, {check['column']}")
    if spec.get('footnoted_values'):
        frame = frame.astype(object)
    for cell in spec.get('footnoted_values', []):
        raw = text(frame.iat[cell['row'], cell['column']])
        match = re.fullmatch(r'(.+?)\((\d+)\)', raw)
        if raw != cell['raw'] or match is None or match[2] != cell['footnote']:
            raise ValueError('Изменилась проверенная числовая ячейка со сноской')
        frame.iat[cell['row'], cell['column']] = number(match[1])
    frequency = spec.get('frequency', 'monthly')
    if frequency not in ('monthly', 'quarterly'):
        raise ValueError('Поддерживаются только monthly и quarterly')
    if spec['layout'] in ('year_columns', 'month_columns', 'quarter_columns'):
        header = spec['header_row']
        mapping = spec[spec['layout']]
        reader = (year_number if spec['layout'] == 'year_columns' else
                  quarter_number if spec['layout'] == 'quarter_columns' else month_number)
        for col, expected in mapping.items():
            if reader(frame.iat[header, int(col)]) != int(expected):
                raise ValueError(f'Изменился заголовок столбца {col}; перепроверьте раскладку')
    if spec['layout'] == 'cells':
        result = extract_mapped_cells(frame, spec, frequency)
    else:
        result = extract_monthly(frame, spec) if frequency == 'monthly' else extract_quarterly(frame, spec)
    if spec.get('trim_empty_edges'):
        observed = result.loc[result.value.notna(), 'period']
        result = result.loc[result.period.between(observed.min(), observed.max())].reset_index(drop=True)
    for key in ('indicator', 'unit', 'basis', 'territory'):
        result[key] = spec[key]
    result['source_url'] = meta['url']
    result['source_sha256'] = meta['sha256']
    result['retrieved_at'] = meta['retrieved_at']
    result['sheet'] = spec['sheet']
    result['frequency'] = frequency
    result['acquisition'] = meta.get('acquisition', 'https_download')
    result['methodology_note'] = spec.get('methodology_note', '')
    result['release_date'] = pd.NaT  # HTTP Last-Modified is NOT an observation's release date.
    return result


def extract_mapped_cells(frame, spec, frequency):
    """Read reviewed cells for horizontal quarters and single-month releases."""
    records = []
    for cell in spec['cells']:
        period = pd.Timestamp(cell['period'])
        rule = 'M' if frequency == 'monthly' else 'Q'
        if period != period.to_period(rule).start_time:
            raise ValueError('Дата ячейки должна быть началом периода')
        records.append((period, number(frame.iat[cell['row'], cell['column']])))
    result = pd.DataFrame(records, columns=['period', 'value']).sort_values('period')
    if result.empty or not result.value.notna().any():
        raise ValueError('Не извлечено ни одного значения')
    if result.period.duplicated().any():
        raise ValueError('Повторяющиеся периоды в разметке ячеек')
    if any(not math.isfinite(v) for v in result.value.dropna()):
        raise ValueError('Бесконечные значения')
    return result.reset_index(drop=True)


def combine(parts):
    if not parts:
        raise ValueError('Нет разобранных источников; пустой датасет не экспортируется')
    data = pd.concat(parts, ignore_index=True)
    if data.duplicated(['indicator', 'period']).any():
        raise ValueError('Перекрывающиеся источники; выберите одну версию каждого ряда')
    for indicator, group in data.groupby('indicator'):
        keys = ['unit', 'basis', 'territory'] + (['frequency'] if 'frequency' in group else [])
        if any(group[key].nunique() != 1 for key in keys):
            raise ValueError(f'Несовместимые измерения у {indicator}')
    return data.sort_values(['indicator', 'period']).reset_index(drop=True)


def extract_quarterly(frame, spec):
    """Extract separate quarters. Cumulative half-year/nine-month totals fail closed."""
    rows, records = spec['rows'], []
    if not rows or len(rows) != len(set(rows)):
        raise ValueError('rows должен содержать неповторяющиеся номера строк')
    layout = spec['layout']
    for row in rows:
        if layout == 'year_columns':
            quarter = quarter_number(frame.iat[row, spec['quarter_col']])
            if quarter is None:
                raise ValueError(f'Строка {row}: ожидался отдельный квартал')
            for col, year in spec['year_columns'].items():
                records.append((pd.Timestamp(year=int(year), month=3 * quarter - 2, day=1),
                                number(frame.iat[row, int(col)])))
        elif layout == 'quarter_columns':
            year = year_number(frame.iat[row, spec['year_col']])
            if year is None:
                raise ValueError(f'Строка {row}: ожидался год')
            for col, quarter in spec['quarter_columns'].items():
                if int(quarter) not in (1, 2, 3, 4):
                    raise ValueError('Номер квартала должен быть от 1 до 4')
                records.append((pd.Timestamp(year=year, month=3 * int(quarter) - 2, day=1),
                                number(frame.iat[row, int(col)])))
        elif layout == 'long':
            raw = frame.iat[row, spec['date_col']]
            if not isinstance(raw, (datetime, pd.Timestamp)) and not re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?', text(raw)):
                raise ValueError(f'Строка {row}: нужна дата Excel или ISO')
            records.append((pd.Timestamp(raw).to_period('Q').start_time,
                            number(frame.iat[row, spec['value_col']])))
        else:
            raise ValueError(f'Неизвестная квартальная раскладка: {layout}')
    result = pd.DataFrame(records, columns=['period', 'value']).sort_values('period')
    if result.empty or result.value.notna().sum() == 0:
        raise ValueError('Не извлечено ни одного значения')
    if result.period.isna().any() or result.period.duplicated().any():
        raise ValueError('Отсутствующие или повторяющиеся кварталы')
    if any(not math.isfinite(v) for v in result.value.dropna()):
        raise ValueError('Бесконечные значения')
    return result.reset_index(drop=True)


def suggest_layouts(frame):
    """Structural suggestions only; the user must verify indicator, unit and basis."""
    suggestions = []
    for header in range(min(40, len(frame))):
        years = {col: year_number(frame.iat[header, col]) for col in range(frame.shape[1])}
        years = {col: year for col, year in years.items() if year is not None}
        if years:
            for month_col in range(min(4, frame.shape[1])):
                rows = [row for row in range(header + 1, len(frame))
                        if month_number(frame.iat[row, month_col]) is not None]
                if rows:
                    suggestions.append({'layout': 'year_columns', 'header_row': header,
                                        'month_col': month_col, 'year_columns': years, 'rows': rows})
        months = {col: month_number(frame.iat[header, col]) for col in range(frame.shape[1])}
        months = {col: month for col, month in months.items() if month is not None}
        if len(months) >= 2:
            for year_col in range(min(4, frame.shape[1])):
                rows = [row for row in range(header + 1, len(frame))
                        if year_number(frame.iat[row, year_col]) is not None]
                if rows:
                    suggestions.append({'layout': 'month_columns', 'header_row': header,
                                        'year_col': year_col, 'month_columns': months, 'rows': rows})
    return suggestions


def import_local_excel(path, url, root):
    """Import a user-downloaded file, retaining its origin claim and original bytes."""
    official_url(url)
    path, root = Path(path), Path(root)
    payload = path.read_bytes()
    if payload.startswith(b'PK\x03\x04'):
        suffix = '.xlsx'
    elif payload.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):
        suffix = '.xls'
    else:
        raise ValueError(f'{path.name}: не является XLS/XLSX')
    # A ZIP signature alone could describe an HTML page archive or unrelated ZIP.
    with pd.ExcelFile(path) as book:
        if not book.sheet_names:
            raise ValueError(f'{path.name}: в книге нет листов')
    digest = hashlib.sha256(payload).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    target = root / (digest + suffix)
    target.write_bytes(payload)
    meta = {'url': url, 'file': target.name, 'sha256': digest, 'bytes': len(payload),
            'retrieved_at': datetime.now(timezone.utc).isoformat(),
            'acquisition': 'user_supplied_file', 'local_file_name': path.name,
            'origin_note': 'URL matched by selected filename; not verified by an HTTPS download.'}
    key = hashlib.sha256(url.encode()).hexdigest()
    (root / f'{key}.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return target, meta


def download_selected(catalog, raw_dir, local_dir=None, refresh=False, http=None, delay=1.0,
                      allow_network=True):
    """Download reviewed URLs or import exact local filenames; report every outcome.

    Stop network attempts after the proxy's shared TLS failure, while continuing
    local imports and verified cache reads. No fake/empty statistical files result.
    """
    raw_dir = Path(raw_dir)
    local_dir = Path(local_dir) if local_dir is not None else None
    if local_dir is not None and catalog.groupby('file_name').url.nunique().gt(1).any():
        raise ValueError('Неоднозначные имена локальных файлов: несколько URL с одним именем')
    http = http if http is not None else session(retries=0)
    downloads, reports, tls_blocked = {}, [], False
    for row in catalog.drop_duplicates('url').to_dict('records'):
        url, name = official_url(row['url']), row['file_name']
        if Path(name).name != name:
            raise ValueError('file_name должен быть именем файла без директорий')
        report = {'source_id': row.get('source_id', ''), 'file_name': name, 'url': url}
        local = local_dir / name if local_dir is not None else None
        receipt = raw_dir / f'{hashlib.sha256(url.encode()).hexdigest()}.json'
        try:
            if local is not None and local.is_file():
                path, meta = import_local_excel(local, url, raw_dir)
                report['status'] = 'imported'
            elif receipt.exists() and not refresh:
                path, meta = snapshot(None, url, raw_dir)
                report['status'] = 'cached'
            elif not allow_network:
                report['status'] = 'not_attempted_offline'
                reports.append(report)
                continue
            elif tls_blocked:
                report['status'] = 'not_attempted_proxy_tls'
                reports.append(report)
                continue
            else:
                try:
                    path, meta = snapshot(http, url, raw_dir, refresh=refresh)
                finally:
                    time.sleep(delay)
                report['status'] = 'downloaded'
            downloads[url] = (path, meta)
            report['sha256'] = meta['sha256']
        except ProxyTLSFailure as exc:
            tls_blocked = True
            report.update(status='failed_proxy_tls', error=str(exc))
        except (requests.RequestException, ValueError, OSError) as exc:
            report.update(status='failed', error=str(exc))
        reports.append(report)
    return downloads, pd.DataFrame(reports)
