import json
import zipfile

import pandas as pd

from local_check import run_check


def test_offline_local_check_packages_actual_workbook(tmp_path):
    local = tmp_path / 'input'
    local.mkdir()
    frame = pd.DataFrame([['ИПЦ', 2024], ['Январь', 100.8], ['Февраль', 100.6]])
    workbook = local / 'cpi.xlsx'
    frame.to_excel(workbook, index=False, header=False)
    catalog = tmp_path / 'catalog.csv'
    pd.DataFrame([{'role': 'primary', 'file_name': 'cpi.xlsx',
                   'url': 'https://rosstat.gov.ru/cpi.xlsx'}]).to_csv(catalog, index=False)
    status, output = run_check(tmp_path, catalog, local, offline=True)
    assert status == 0
    report = json.loads((output / 'report.json').read_text())
    assert report['workbooks_read'] == 1
    assert report['normalized_time_series_created'] is False
    assert report['workbooks'][0]['sheets'][0]['rows'] == 3
    assert report['workbooks'][0]['sheets'][0]['preview']['data'][1] == ['Январь', 100.8]
    with zipfile.ZipFile(output / 'results.zip') as archive:
        assert archive.read('files/cpi.xlsx') == workbook.read_bytes()
        assert set(archive.namelist()) == {'report.json', 'selected_sources.csv',
                                         'files/cpi.xlsx', 'previews/cpi.xlsx/sheet_001.csv'}


def test_missing_file_is_not_a_successful_parse(tmp_path):
    catalog = tmp_path / 'catalog.csv'
    pd.DataFrame([{'role': 'primary', 'file_name': 'missing.xlsx',
                   'url': 'https://rosstat.gov.ru/missing.xlsx'}]).to_csv(catalog, index=False)
    status, output = run_check(tmp_path, catalog, tmp_path / 'input', offline=True)
    report = json.loads((output / 'report.json').read_text())
    assert status == 2
    assert report['files_available'] == 0
    assert report['download_and_excel_read_succeeded'] is False
    with zipfile.ZipFile(output / 'results.zip') as archive:
        assert not any(name.startswith('files/') for name in archive.namelist())
