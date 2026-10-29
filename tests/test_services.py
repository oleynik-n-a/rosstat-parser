import pandas as pd
import pytest

from extract_dataset import extraction_specs
from rosstat_parser import parse_source


@pytest.mark.parametrize('region_row,label', [(6, 'Россия*'), (7, 'Россия')])
def test_services_archive_layouts_keep_monthly_bases(tmp_path, region_row, label):
    (tmp_path / 'sources').mkdir()
    (tmp_path / 'sources/extraction_specs.json').write_text('[]')
    frame = pd.DataFrame('', index=range(9), columns=range(5), dtype=object)
    frame.iat[1, 0] = 'Основные показатели платных услуг населению за январь 2022 г.'
    frame.iat[2, 0] = 'Платные услуги населению'
    frame.iat[4, 2] = 'Индекс физического объема % к (в сопоставимых ценах):'
    frame.iat[5, 2] = 'предыдущему месяцу'
    frame.iat[5, 3] = 'соответствующему месяцу прошлого года'
    frame.iat[5, 4] = 'соответствующему периоду прошлого года'
    frame.iat[region_row, 0] = label
    frame.iat[8, 0] = 'Центральный федеральный округ'
    frame.iloc[region_row, 2:5] = [95.7, 114.7, 200.0]
    path = tmp_path / 'services.xlsx'
    frame.to_excel(path, sheet_name='платные всего', header=False, index=False)
    url = 'https://rosstat.gov.ru/services.xlsx'
    catalog = pd.DataFrame([dict(url=url, file_name=path.name, title='Январь',
                                 period_label='2022 год', indicator_hint='paid_services')])
    meta = dict(url=url, sha256='fixture', retrieved_at='2026-10-29')
    specs = extraction_specs(tmp_path, catalog, {url: (path, meta)}, services_history=True)
    values = [parse_source(path, meta, spec).value.iloc[0] for spec in specs]
    assert values == [95.7, 114.7]
    assert all(spec['cells'][0]['period'] == '2022-01-01' for spec in specs)
    frame.iat[5, 3] = 'соответствующему периоду прошлого года'
    frame.to_excel(path, sheet_name='платные всего', header=False, index=False)
    with pytest.raises(ValueError, match='база индекса'):
        extraction_specs(tmp_path, catalog, {url: (path, meta)}, services_history=True)
