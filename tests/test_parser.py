import hashlib
import json
from datetime import datetime

import pandas as pd
import pytest

from rosstat_parser import (number, month_number, links_from_html, extract_monthly,
                            parse_source, combine, snapshot, suggest_layouts)


@pytest.mark.parametrize('raw,expected', [('1\xa0234,5', 1234.5), ('−2,1', -2.1), (0, 0)])
def test_numbers(raw, expected):
    assert number(raw) == expected


@pytest.mark.parametrize('raw', ['—', '…', None])
def test_missing(raw):
    assert pd.isna(number(raw))


@pytest.mark.parametrize('raw', ['100,2 1)', '25%', 'итого', 'inf'])
def test_ambiguous_numbers(raw):
    with pytest.raises(ValueError):
        number(raw)


def test_cumulative_is_not_month():
    assert month_number('Январь-март') is None
    assert month_number('Январь') == 1


def test_links():
    html = '<a href="/storage/a.xlsx">ИПЦ</a><a href="https://other.ru/a.xls">x</a>'
    links = links_from_html(html, 'https://rosstat.gov.ru/statistics/price', 'prices')
    assert len(links) == 1
    assert links[0]['url'] == 'https://rosstat.gov.ru/storage/a.xlsx'


def test_excel_pipeline(tmp_path):
    path = tmp_path / 'source.xlsx'
    frame = pd.DataFrame([['ИПЦ РФ, к предыдущему месяцу', None, None],
                          [None, 2023, 2024], ['Январь', '100,8', 100.7],
                          ['Февраль', '100,4', None], ['Январь-февраль', 101.2, 101.0]])
    frame.to_excel(path, header=False, index=False)
    spec = dict(indicator='cpi_mom_index', unit='%', basis='предыдущий месяц = 100',
                territory='Российская Федерация', sheet='Sheet1', expected_text=['ИПЦ', 'предыдущему месяцу'],
                layout='year_columns', header_row=1, month_col=0, year_columns={1: 2023, 2: 2024}, rows=[2, 3])
    meta = dict(url='https://rosstat.gov.ru/test.xlsx', sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                retrieved_at='2026-10-24T00:00:00+00:00')
    result = parse_source(path, meta, spec)
    assert len(result) == 4 and result['value'].isna().sum() == 1
    assert result.iloc[0]['value'] == 100.8
    assert result['release_date'].isna().all()
    assert len(combine([result])) == 4
    assert suggest_layouts(frame)[0]['rows'] == [2, 3]
    with pytest.raises(ValueError, match='Перекрывающиеся'):
        combine([result, result])
    with pytest.raises(ValueError, match='заголовок'):
        parse_source(path, meta, dict(spec, year_columns={1: 2022, 2: 2024}))
    with pytest.raises(ValueError, match='контрольные'):
        parse_source(path, meta, dict(spec, expected_text=['безработица']))


def test_month_columns_and_long():
    frame = pd.DataFrame([[2024, '3,2', '—'], [2025, 3.0, 2.9]])
    result = extract_monthly(frame, dict(layout='month_columns', year_col=0,
                                        month_columns={1: 1, 2: 2}, rows=[0, 1]))
    assert result['period'].dt.month.tolist() == [1, 2, 1, 2]
    long = pd.DataFrame([['2024-03', 2.7], [datetime(2024, 4, 30), 2.6]])
    assert extract_monthly(long, dict(layout='long', date_col=0, value_col=1, rows=[0, 1]))['value'].tolist() == [2.7, 2.6]


def test_duplicates_fail():
    with pytest.raises(ValueError, match='Повторяющиеся'):
        extract_monthly(pd.DataFrame([['Январь', 1], ['Январь', 2]]),
                        dict(layout='year_columns', month_col=0, year_columns={1: 2024}, rows=[0, 1]))


def test_cache_and_html_rejection(tmp_path):
    class Response:
        status_code = 200
        headers = {}
        url = 'https://rosstat.gov.ru/a.xlsx'
        def raise_for_status(self):
            pass
    class Session:
        def get(self, *args, **kwargs):
            return response
    response = Response()
    real = tmp_path / 'test.xlsx'
    pd.DataFrame([[1]]).to_excel(real, index=False)
    response.content = real.read_bytes()
    path, meta = snapshot(Session(), response.url, tmp_path / 'cache')
    assert snapshot(None, response.url, tmp_path / 'cache')[1] == meta
    path.write_bytes(b'bad')
    with pytest.raises(ValueError, match='Поврежден кеш'):
        snapshot(None, response.url, tmp_path / 'cache')
    response.content = b'<html>anti-bot</html>'
    with pytest.raises(ValueError, match='не является'):
        snapshot(Session(), response.url, tmp_path / 'html')


def test_saved_rosstat_document_cards(tmp_path):
    from rosstat_parser import catalog_from_saved_html
    path = tmp_path / 'page.html'
    path.write_text('''<meta charset="utf-8"><script>throw Error('must not execute')</script>
    <div class="document-list__item document-list__item--row">
      <div class="document-list__item-link"><a href="/storage/mediabank/a.xlsx">XLSX</a></div>
      <div class="document-list__item-desc">
        <div class="document-list__item-title">Индексы потребительских цен</div>
        <div class="document-list__item-info">12 Кб, 10.10.2026</div>
      </div>
    </div>
    <a href="/storage/mediabank/a.xlsx">Повтор</a>
    <a href="https://evil.example/b.xlsx">Чужой файл</a>''', encoding='utf-8')
    result = catalog_from_saved_html(path, 'https://rosstat.gov.ru/folder/11186')
    assert len(result) == 1
    assert result.iloc[0].title == 'Индексы потребительских цен'
    assert '10.10.2026' in result.iloc[0].context
    assert result.iloc[0].url == 'https://rosstat.gov.ru/storage/mediabank/a.xlsx'


def test_month_context_and_comparison_basis(tmp_path):
    from rosstat_parser import catalog_from_saved_html
    path = tmp_path / 'services.html'
    path.write_text('''<meta charset="utf-8">
    <div class="toggle-card"><div class="toggle-card__header"><div class="toggle-card__title">Платные услуги</div></div>
    <div class="content">Основные показатели, месяцы (с 2022 г.)</div>
    <div class="document-list__item document-list__item--list-tree"><li class="list-tree__item">
      <div class="list-tree__item-right"><div class="list-tree__item-header">2026 год</div>
        <div class="document-list__item"><a href="/plat_092026.xls">XLS</a>
          <div class="document-list__item-title">Сентябрь</div>
          <div class="document-list__item-info">558 Кб, 28.10.2026</div></div>
      </div></li></div></div>
    <a href="/basis.xlsx">Индекс к предыдущему году</a>''', encoding='utf-8')
    rows = catalog_from_saved_html(path, 'https://rosstat.gov.ru/uslugi')
    row = rows.iloc[0]
    assert row.period_label == '2026 год'
    assert row.page_date == '28.10.2026'
    assert row.frequency_hint == 'monthly'
    assert 'Платные услуги' in row.display_title and '2026 год' in row.display_title
    assert rows.iloc[1].frequency_hint == 'unknown'  # Basis is not observation frequency.


def test_quarterly_pipeline_and_cumulative_rejection(tmp_path):
    from rosstat_parser import quarter_number, extract_quarterly
    assert quarter_number('IV квартал') == 4
    assert quarter_number(2.0) == 2
    assert quarter_number('I–II квартал') is None
    frame = pd.DataFrame([['ВВП в сопоставимых ценах', None, None],
                          [None, 2023, 2024], ['I квартал', 101.1, '102,1'],
                          ['II квартал', 100.2, '—'], ['Январь-июнь', 102, 103]])
    path = tmp_path / 'gdp.xlsx'
    frame.to_excel(path, header=False, index=False)
    spec = dict(indicator='gdp', unit='%', basis='предыдущий год = 100',
                territory='Российская Федерация', sheet='Sheet1', expected_text=['ВВП', 'сопоставимых'],
                frequency='quarterly', layout='year_columns', header_row=1, quarter_col=0,
                year_columns={1: 2023, 2: 2024}, rows=[2, 3])
    meta = dict(url='https://rosstat.gov.ru/gdp.xlsx', sha256='test', retrieved_at='2026-10-29')
    result = parse_source(path, meta, spec)
    assert result.frequency.eq('quarterly').all()
    assert result.period.dt.month.tolist() == [1, 4, 1, 4]
    assert result.value.isna().sum() == 1
    with pytest.raises(ValueError, match='отдельный квартал'):
        extract_quarterly(frame, dict(spec, rows=[4]))
    other = pd.DataFrame([[2024, '101,2', 102.3]])
    wide = extract_quarterly(other, dict(layout='quarter_columns', year_col=0,
                                        quarter_columns={1: 1, 2: 4}, rows=[0]))
    assert wide.period.dt.month.tolist() == [1, 10]
    long = pd.DataFrame([['2024-06', 1]])
    assert extract_quarterly(long, dict(layout='long', date_col=0, value_col=1, rows=[0])).period.iloc[0] == pd.Timestamp('2024-04-01')


def test_local_import_continues_after_proxy_failure(tmp_path):
    from rosstat_parser import download_selected
    class Response:
        status_code = 503
        content = b'upstream TLS_error: CERTIFICATE_VERIFY_FAILED'
    class Http:
        def __init__(self):
            self.calls = 0
        def get(self, *args, **kwargs):
            self.calls += 1
            return Response()
    local = tmp_path / 'input'
    local.mkdir()
    pd.DataFrame([['Январь', 100.1]]).to_excel(local / 'local.xlsx', header=False, index=False)
    sources = pd.DataFrame([{'file_name': name, 'url': f'https://rosstat.gov.ru/{name}'}
                            for name in ['first.xlsx', 'local.xlsx', 'third.xlsx']])
    http = Http()
    files, report = download_selected(sources, tmp_path / 'raw', local_dir=local, http=http, delay=0)
    assert http.calls == 1
    assert report.status.tolist() == ['failed_proxy_tls', 'imported', 'not_attempted_proxy_tls']
    assert len(files) == 1
    meta = files['https://rosstat.gov.ru/local.xlsx'][1]
    assert meta['acquisition'] == 'user_supplied_file'
    assert meta['sha256'] == hashlib.sha256((local / 'local.xlsx').read_bytes()).hexdigest()
    files, report = download_selected(sources.iloc[1:2], tmp_path / 'raw', http=http,
                                      allow_network=False, delay=0)
    assert report.status.tolist() == ['cached']
    assert http.calls == 1
    _, report = download_selected(sources.iloc[:1], tmp_path / 'other', http=http,
                                  allow_network=False, delay=0)
    assert report.status.tolist() == ['not_attempted_offline']
    assert http.calls == 1


def test_year_footnotes_are_labels_not_new_periods():
    from rosstat_parser import year_number
    assert year_number('2023(2)') == 2023
    assert year_number('20231)') == 2023
    assert year_number('2026*') == 2026
    assert year_number('2023–2024') is None
    assert year_number('2023 год, январь–март') is None


def test_reviewed_cells_validate_headers_and_trim_unpublished_quarters(tmp_path):
    from rosstat_parser import extract_mapped_cells
    frame = pd.DataFrame([['ВВП в ценах 2021 г.', None, None],
                          ['I квартал', 'II квартал', 'III квартал'],
                          [100.0, 101.5, None]])
    path = tmp_path / 'quarters.xlsx'
    frame.to_excel(path, index=False, header=False)
    spec = dict(indicator='gdp', unit='млрд рублей', basis='цены 2021 года',
                territory='Российская Федерация', sheet='Sheet1',
                expected_text=['ВВП в ценах 2021 г.'], frequency='quarterly', layout='cells',
                cells=[dict(row=2, column=c, period=f'2026-{3*c+1:02d}-01') for c in range(3)],
                expected_cells=[dict(row=1, column=1, text='II квартал')], trim_empty_edges=True)
    meta = dict(url='https://rosstat.gov.ru/gdp.xlsx', sha256='test', retrieved_at='2026-10-29')
    result = parse_source(path, meta, spec)
    assert result.period.tolist() == [pd.Timestamp('2026-01-01'), pd.Timestamp('2026-04-01')]
    assert result.value.tolist() == [100.0, 101.5]
    with pytest.raises(ValueError, match='контрольная ячейка'):
        parse_source(path, meta, dict(spec, expected_cells=[dict(row=1, column=1, text='IV квартал')]))
    with pytest.raises(ValueError, match='Повторяющиеся'):
        extract_mapped_cells(frame, dict(cells=[spec['cells'][0]] * 2), 'quarterly')
    with pytest.raises(ValueError, match='началом периода'):
        extract_mapped_cells(frame, dict(cells=[dict(row=2, column=0, period='2026-02-01')]), 'quarterly')


def test_only_explicitly_reviewed_numeric_footnotes_are_removed(tmp_path):
    frame = pd.DataFrame([['Зарплата', 'Октябрь'], [2022, '62 470(2)']])
    path = tmp_path / 'wages.xlsx'
    frame.to_excel(path, header=False, index=False)
    spec = dict(indicator='wages', unit='рублей', basis='в месяц',
                territory='Российская Федерация', sheet='Sheet1', expected_text=['Зарплата'],
                layout='month_columns', header_row=0, year_col=0, month_columns={1: 10}, rows=[1])
    meta = dict(url='https://rosstat.gov.ru/wages.xlsx', sha256='fixture', retrieved_at='2026-10-29')
    with pytest.raises(ValueError, match='Нераспознанное число'):
        parse_source(path, meta, spec)
    reviewed = dict(row=1, column=1, raw='62 470(2)', footnote='2')
    assert parse_source(path, meta, dict(spec, footnoted_values=[reviewed])).value.iloc[0] == 62470
    with pytest.raises(ValueError, match='ячейка со сноской'):
        parse_source(path, meta, dict(spec, footnoted_values=[dict(reviewed, raw='63 060(2)')]))
