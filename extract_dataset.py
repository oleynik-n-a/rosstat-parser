"""Extract reviewed Rosstat series locally, with separate monthly/quarterly outputs."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from rosstat_parser import combine, download_selected, month_number, parse_source, text


def extraction_specs(root, catalog, downloads, services_history=False):
    specs = json.loads((Path(root) / 'sources/extraction_specs.json').read_text())
    if not services_history:
        return specs
    specs = [s for s in specs if not s['indicator'].startswith('paid_services_')]
    services = catalog.loc[catalog.indicator_hint.eq('paid_services')]
    for row in services.to_dict('records'):
        month = month_number(row['title'])
        years = re.findall(r'(?:19|20)\d{2}', row['period_label'])
        if month is None or len(set(years)) != 1:
            raise ValueError(f"Неоднозначный месяц выпуска: {row['file_name']}")
        year = int(years[0])
        path, _ = downloads[row['url']]
        frame = pd.read_excel(path, sheet_name='платные всего', header=None)
        heading = text(frame.iat[1, 0]).casefold()
        # Releases through 2024 contain an extra numbered header row.
        region_rows = [r for r in (6, 7) if re.fullmatch(
            r'(?:Россия|Российская Федерация)(?:\*+|\d+\)|\(\d+\))?', text(frame.iat[r, 0]))]
        if (str(year) not in heading or row['title'].casefold() not in heading
                or 'основные показатели платных услуг населению' not in heading
                or text(frame.iat[2, 0]).casefold() != 'платные услуги населению'
                or len(region_rows) != 1
                or 'Индекс физического объема' not in text(frame.iat[4, 2])):
            raise ValueError(f"Изменилась структура выпуска услуг: {row['file_name']}")
        region_row = region_rows[0]
        for col, slug, label, basis in [
            (2, 'mom', 'предыдущему месяцу', 'предыдущий месяц = 100'),
            (3, 'yoy', 'соответствующему месяцу прошлого года',
             'соответствующий месяц предыдущего года = 100'),
        ]:
            if text(frame.iat[5, col]) != label:
                raise ValueError(f"Изменилась база индекса: {row['file_name']}")
            specs.append(dict(
                url=row['url'], file_name=row['file_name'], sheet='платные всего',
                indicator=f'paid_services_volume_{slug}_index', frequency='monthly',
                unit='%', basis=basis, territory='Российская Федерация', layout='cells',
                cells=[dict(row=region_row, column=col, period=f'{year}-{month:02d}-01')],
                expected_text=['Основные показатели платных услуг населению',
                               'Индекс физического объема'],
                expected_cells=[dict(row=r, column=c, text=text(frame.iat[r, c]))
                                for r, c in [(1, 0), (2, 0), (4, 2), (5, col), (region_row, 0)]],
                methodology_note='Индекс за отдельный месяц из соответствующего выпуска; '
                                 'накопительный индекс исключён. История из выпусков может '
                                 'отличаться от позднейших пересмотренных рядов.',
            ))
    return specs


def verify_cpi_components(catalog, downloads, macro):
    """Compare the long history with independently laid out national rows."""
    source = catalog.loc[catalog.file_name.eq('ipc_RF_fo_sub_09-2026.xlsx')].iloc[0]
    path, _ = downloads[source.url]
    indicators = ['cpi_mom_index', 'cpi_food_mom_index',
                  'cpi_nonfood_mom_index', 'cpi_services_mom_index']
    values = macro.set_index(['indicator', 'period']).value
    checked = 0
    with pd.ExcelFile(path) as book:
        for sheet in book.sheet_names:
            match = re.fullmatch(r'(\d{2})\((\d{4})\)', sheet)
            if match is None:
                continue
            month, year = map(int, match.groups())
            frame = pd.read_excel(book, sheet_name=sheet, header=None, nrows=5)
            if (text(frame.iat[4, 1]) != 'Российская Федерация'
                    or 'к предыдущему месяцу' not in text(frame.iat[1, 2])):
                raise ValueError(f'Изменилась контрольная таблица ИПЦ: {sheet}')
            for col, indicator in enumerate(indicators, 2):
                expected = values.loc[(indicator, pd.Timestamp(year=year, month=month, day=1))]
                if abs(float(frame.iat[4, col]) - expected) > 1e-8:
                    raise ValueError(f'Не совпали независимые таблицы ИПЦ: {sheet}, {indicator}')
                checked += 1
    return dict(independent_cpi_values_checked=checked, matched=True)


def export_dataset(root, catalog, downloads, specs, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    parts = [parse_source(*downloads[s['url']], s) for s in specs]
    macro = combine(parts)
    crosscheck = verify_cpi_components(catalog, downloads, macro)
    quality = []
    for indicator, group in macro.groupby('indicator'):
        frequency = group.frequency.iloc[0]
        calendar = pd.date_range(group.period.min(), group.period.max(),
                                 freq={'monthly': 'MS', 'quarterly': 'QS'}[frequency])
        values = group.set_index('period').value.reindex(calendar)
        quality.append(dict(indicator=indicator, frequency=frequency,
                            start=calendar.min(), end=calendar.max(),
                            observed=int(values.notna().sum()), missing=int(values.isna().sum())))
    quality = pd.DataFrame(quality)
    macro.to_csv(output / 'macro_long.csv', index=False, date_format='%Y-%m-%d')
    quality.to_csv(output / 'quality_report.csv', index=False, date_format='%Y-%m-%d')
    for frequency, group in macro.groupby('frequency'):
        wide = group.pivot(index='period', columns='indicator', values='value').sort_index()
        calendar = pd.date_range(wide.index.min(), wide.index.max(),
                                 freq={'monthly': 'MS', 'quarterly': 'QS'}[frequency])
        wide.reindex(calendar).rename_axis('period').to_csv(
            output / f'macro_{frequency}.csv', date_format='%Y-%m-%d')
    (output / 'extraction_specs.json').write_text(json.dumps(specs, ensure_ascii=False, indent=2))
    (output / 'validation.json').write_text(json.dumps(crosscheck, ensure_ascii=False, indent=2))
    notes = []
    for name, group in pd.DataFrame(specs).groupby('file_name'):
        url = group.url.iloc[0]
        source_notes = set()
        for sheet in group.sheet.unique():
            frame = pd.read_excel(downloads[url][0], sheet_name=sheet, header=None)
            for value in frame.to_numpy().ravel():
                label = text(value)
                if len(label) > 70 and re.search(
                        'Без учета|учетом|деноминац|пересматрив|Данные содержат', label, re.I):
                    source_notes.add(label)
        notes.append(dict(file_name=name, url=url, source_notes=sorted(source_notes)))
    notes.append(dict(file_name='IFO_rozn_2025.xls', verified_frequency='annual',
                      included_in_monthly_dataset=False,
                      reason='В книге годовые индексы, а не отдельные месяцы.'))
    (output / 'source_assessment.json').write_text(json.dumps(notes, ensure_ascii=False, indent=2))
    return macro, quality


def run(root, offline=False, services_history=False):
    root = Path(root)
    catalog = pd.read_csv(root / 'sources/macro_selected.csv').fillna('')
    roles = ['primary', 'services_history'] if services_history else ['primary']
    selected = catalog.loc[catalog.role.isin(roles)]
    output = root / 'data/processed' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output.mkdir(parents=True)
    selected.to_csv(output / 'selected_sources.csv', index=False)
    downloads, report = download_selected(selected, root / 'data/raw',
                                           local_dir=root / 'data/input/excel',
                                           allow_network=not offline)
    report.to_json(output / 'download_report.json', orient='records', force_ascii=False, indent=2)
    if len(downloads) != selected.url.nunique():
        raise ValueError(f'Получено {len(downloads)} из {selected.url.nunique()} файлов; отчёт: {output}')
    specs = extraction_specs(root, selected, downloads, services_history)
    macro, quality = export_dataset(root, selected, downloads, specs, output)
    print(quality.to_string(index=False))
    print(f'Извлечено {macro.value.notna().sum()} значений; показатели: {macro.indicator.nunique()}')
    print(f'Результаты: {output}')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--services-history', action='store_true', help='Include all 57 reviewed monthly releases')
    args = parser.parse_args()
    run(Path(__file__).resolve().parent, args.offline, args.services_history)


if __name__ == '__main__':
    main()
