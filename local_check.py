"""Check actual Rosstat downloads and Excel readability; package evidence for review."""
from __future__ import annotations

import argparse
import json
import platform
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from rosstat_parser import download_selected, suggest_layouts


def inspect_workbook(path, output):
    """Read all sheets; layout hints and cell previews do not assert economic meaning."""
    output.mkdir(parents=True, exist_ok=True)
    sheets = []
    with pd.ExcelFile(path) as book:
        for index, name in enumerate(book.sheet_names):
            frame = pd.read_excel(book, sheet_name=name, header=None)
            preview = frame.iloc[:40, :40]
            preview_name = f'sheet_{index + 1:03d}.csv'
            preview.to_csv(output / preview_name, index=False, header=False)
            # Bound structural scanning while reading the actual sheet in full above.
            hints = suggest_layouts(frame.head(250))
            sheets.append({
                'sheet': name, 'rows': len(frame), 'columns': len(frame.columns),
                'nonempty_cells': int(frame.notna().to_numpy().sum()),
                'preview_file': preview_name,
                'preview': json.loads(preview.to_json(orient='split', date_format='iso')),
                'layout_scan_rows': min(250, len(frame)),
                'monthly_layout_candidates': hints[:10],
                'total_layout_candidates': len(hints),
            })
    if not sheets:
        raise ValueError('В книге нет листов')
    return sheets


def run_check(root, catalog_path, local_dir, offline=False):
    root = Path(root)
    sources = pd.read_csv(catalog_path)
    selected = sources.loc[sources.role.eq('primary')].copy()
    if selected.empty:
        raise ValueError('В каталоге нет роли primary')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = root / 'data' / 'local_runs' / run_id
    output.mkdir(parents=True)
    selected.to_csv(output / 'selected_sources.csv', index=False)
    print(f'Python {platform.python_version()}; {platform.system()}')
    print(f'Проверяем файлов: {len(selected)}. Каталог результатов: {output}', flush=True)
    downloads, download_report = download_selected(
        selected, root / 'data' / 'raw', local_dir=local_dir, allow_network=not offline,
    )
    print(download_report[['file_name', 'status']].to_string(index=False), flush=True)
    books = []
    for row in selected.drop_duplicates('url').to_dict('records'):
        if row['url'] not in downloads:
            continue
        path, metadata = downloads[row['url']]
        book = {'file_name': row['file_name'], 'url': row['url'], 'metadata': metadata}
        try:
            sheets = inspect_workbook(path, output / 'previews' / row['file_name'])
            book.update(status='read_ok', sheets=sheets)
            print(f"Прочитан {row['file_name']}: {len(sheets)} листов", flush=True)
        except Exception as exc:
            # Preserve evidence for a corrupt/unreadable real workbook and continue others.
            book.update(status='read_failed', error_type=type(exc).__name__, error=str(exc))
            print(f"Ошибка чтения {row['file_name']}: {exc}", flush=True)
        books.append(book)
    ready = (len(downloads) == selected.url.nunique() and bool(books)
             and all(book['status'] == 'read_ok' for book in books))
    report = {
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'python_version': platform.python_version(), 'platform': platform.system(),
        'offline': offline, 'files_selected': int(selected.url.nunique()),
        'files_available': len(downloads),
        'workbooks_read': sum(book['status'] == 'read_ok' for book in books),
        'download_and_excel_read_succeeded': ready,
        'normalized_time_series_created': False,
        'note': 'Workbook reading and layout hints only. Units, index bases and source layouts still require review.',
        'download_results': json.loads(download_report.to_json(orient='records')),
        'workbooks': books,
    }
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    archive_path = output / 'results.zip'
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(output / 'report.json', 'report.json')
        archive.write(output / 'selected_sources.csv', 'selected_sources.csv')
        for preview in sorted((output / 'previews').rglob('*.csv')):
            archive.write(preview, preview.relative_to(output))
        for row in selected.drop_duplicates('url').to_dict('records'):
            if row['url'] in downloads:
                archive.write(downloads[row['url']][0], 'files/' + row['file_name'])
    print(f'Получено: {len(downloads)}; Excel успешно прочитано: {report["workbooks_read"]}.')
    print('Для извлечения временных рядов: .venv/bin/python extract_dataset.py --offline --services-history')
    print(f'Архив проверки: {archive_path}')
    return (0 if ready else 2), output


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', help='Only existing local files and cache')
    parser.add_argument('--local-dir', type=Path, default=root / 'data' / 'input' / 'excel')
    args = parser.parse_args()
    try:
        status, _ = run_check(root, root / 'sources' / 'macro_selected.csv', args.local_dir, args.offline)
        return status
    except (ValueError, OSError) as exc:
        print(f'Проверка не завершена: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
