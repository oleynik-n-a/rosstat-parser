"""Download reviewed Rosstat files, or import exact filenames from a local folder."""
import argparse
from pathlib import Path

import pandas as pd

from rosstat_parser import download_selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', default='sources/macro_selected.csv')
    parser.add_argument('--role', action='append', help='primary (default), optional, services_history')
    parser.add_argument('--local-dir', default='data/input/excel')
    parser.add_argument('--raw-dir', default='data/raw')
    parser.add_argument('--report', default='data/download_report.json')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--offline', action='store_true', help='Only local files and verified cache reads')
    args = parser.parse_args()
    sources = pd.read_csv(args.catalog)
    chosen = sources[sources.role.isin(args.role or ['primary'])]
    if chosen.empty:
        parser.error('Нет источников для указанных ролей')
    downloads, report = download_selected(chosen, args.raw_dir, local_dir=args.local_dir,
                                          refresh=args.refresh, allow_network=not args.offline)
    output = Path(args.report)
    output.parent.mkdir(parents=True, exist_ok=True)
    report.to_json(output, orient='records', force_ascii=False, indent=2)
    print(report[['file_name', 'status']].to_string(index=False))
    print(f'Доступно файлов: {len(downloads)} / {chosen.url.nunique()}. Отчёт: {output}')
    return 0 if len(downloads) == chosen.url.nunique() else 2


if __name__ == '__main__':
    raise SystemExit(main())
