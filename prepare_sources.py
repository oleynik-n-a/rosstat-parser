"""Rebuild the reviewed macro source inventory from saved Rosstat pages, offline."""
import argparse
import hashlib
import json
import re
from pathlib import Path

import pandas as pd

from rosstat_parser import catalog_from_saved_html


def build(input_dir, output_dir):
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    pages = json.loads((output_dir / 'macro_pages.json').read_text())
    reviews = json.loads((output_dir / 'macro_selection.json').read_text())
    frames, provenance = [], []
    for page in pages:
        path = input_dir / page['input_name']
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != page['input_sha256']:
            raise ValueError(f"Изменилось вложение {path.name}; нужна повторная проверка отбора")
        frame = catalog_from_saved_html(path, page['page_url'], page['topic'])
        frame['input_name'] = path.name
        frame['input_sha256'] = digest
        frames.append(frame)
        provenance.append(dict(page, excel_links=len(frame)))
    catalog = pd.concat(frames, ignore_index=True)
    catalog['role'] = 'not_selected'
    catalog['indicator_hint'] = ''
    catalog['target_frequency'] = ''
    catalog['selection_reason'] = 'Не выбран для первого этапа; ссылка сохранена для последующего анализа.'
    catalog['file_verified'] = False
    for review in reviews:
        match = catalog.topic.eq(review['topic']) & catalog.file_name.eq(review['file_name'])
        if match.sum() != 1:
            raise ValueError(f"Не найден однозначный проверенный источник {review['file_name']}")
        title = catalog.loc[match, 'title'].iloc[0].casefold()
        if not all(token.casefold() in title for token in review['expected_title']):
            raise ValueError(f"Изменился заголовок {review['file_name']}")
        for key in ('role', 'indicator_hint', 'target_frequency', 'selection_reason'):
            catalog.loc[match, key] = review[key]
    # Retain every available monthly paid-services issue for later historical assembly.
    # Selection is based on page context as well as filename, not on a guessed URL.
    archive = (catalog.topic.eq('services') & catalog.file_name.str.match(r'(?i)^plat[_-]\d')
               & catalog.group_title.str.contains('месяц', case=False, na=False)
               & catalog.role.eq('not_selected'))
    catalog.loc[archive, ['role', 'indicator_hint', 'target_frequency', 'selection_reason']] = [
        'services_history', 'paid_services', 'monthly',
        'Архив месячных выпусков с 2022 г.; собирать после проверки структуры и пересмотров, с выбором строки РФ.']
    catalog.to_csv(output_dir / 'macro_catalog.csv', index=False)
    catalog[catalog.role.ne('not_selected')].to_csv(output_dir / 'macro_selected.csv', index=False)
    pd.DataFrame(provenance).to_json(output_dir / 'macro_provenance.json', orient='records', force_ascii=False, indent=2)
    return catalog


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', default='data/input')
    parser.add_argument('--output-dir', default='sources')
    args = parser.parse_args()
    result = build(args.input_dir, args.output_dir)
    print(result.groupby(['topic', 'role']).size().to_string())
    print('Unique Excel URLs:', result.url.nunique())
