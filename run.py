#!/usr/bin/env python3
"""Prepare a local environment and execute the Rosstat notebook on any OS."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parent
NOTEBOOK = ROOT / 'rosstat_macro.ipynb'
ENVIRONMENT = ROOT / '.venv'
MIN_PYTHON = (3, 12)


def execute_notebook(offline=False):
    """Run inside .venv; the notebook itself writes the result files."""
    import nbformat
    from nbclient import NotebookClient

    notebook = nbformat.read(NOTEBOOK, as_version=4)
    original_sources = {}
    if offline:
        for index, cell in enumerate(notebook.cells):
            if cell.cell_type == 'code' and 'ALLOW_NETWORK = True' in cell.source:
                original_sources[index] = cell.source
                cell.source = cell.source.replace('ALLOW_NETWORK = True', 'ALLOW_NETWORK = False')

    def progress(cell, cell_index, **kwargs):
        if cell.cell_type == 'code':
            print(f'Ячейка {cell_index + 1}/{len(notebook.cells)}', flush=True)

    client = NotebookClient(
        notebook,
        kernel_name='rosstat-local',
        timeout=1800,
        resources={'metadata': {'path': str(ROOT)}},
        on_cell_start=progress,
    )
    client.execute()
    # Сохраняем исходные настройки, даже если этот запуск был без сети.
    for index, source in original_sources.items():
        notebook.cells[index].source = source
    nbformat.write(notebook, NOTEBOOK)
    print(f'Готово. Результаты: {ROOT / "results"}', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Подготовить среду и полностью выполнить rosstat_macro.ipynb.')
    parser.add_argument('--offline', action='store_true',
                        help='Использовать уже установленные библиотеки и кеш источников без сети.')
    args = parser.parse_args()
    if sys.version_info < MIN_PYTHON:
        parser.error('Нужен Python 3.12 или новее.')
    if not NOTEBOOK.is_file() or not (ROOT / 'certificates/rosstat_ca_bundle.pem').is_file():
        parser.error('Рядом с run.py должны находиться rosstat_macro.ipynb и папка certificates/.')

    python = ENVIRONMENT / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    try:
        if not python.is_file():
            print(f'Создаю виртуальную среду: {ENVIRONMENT}', flush=True)
            venv.EnvBuilder(with_pip=True).create(ENVIRONMENT)
        version = subprocess.run(
            [str(python), '-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'],
            cwd=ROOT, check=False,
        )
        if version.returncode:
            parser.error('Версия Python в .venv ниже 3.12. Пересоздайте среду на Python 3.12+.')

        environment = os.environ.copy()
        runtime = ROOT / 'data/jupyter'
        for variable, path in {
            'JUPYTER_CONFIG_DIR': runtime / 'config',
            'JUPYTER_DATA_DIR': ENVIRONMENT / 'share/jupyter',
            'JUPYTER_RUNTIME_DIR': runtime / 'runtime',
            'IPYTHONDIR': runtime / 'ipython',
            'MPLCONFIGDIR': runtime / 'matplotlib',
        }.items():
            path.mkdir(parents=True, exist_ok=True)
            environment[variable] = str(path)
        environment['JUPYTER_PATH'] = str(ENVIRONMENT / 'share/jupyter')
        environment['MPLBACKEND'] = 'module://matplotlib_inline.backend_inline'
        environment['PYTHONUNBUFFERED'] = '1'
        if args.offline:
            # Также запрещаем обращения pip к индексу в первой ячейке ноутбука.
            environment['PIP_NO_INDEX'] = '1'
            environment['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'

        runtime_check = subprocess.run(
            [str(python), '-c', 'import jupyterlab, nbclient, nbformat, ipykernel'],
            cwd=ROOT, env=environment, capture_output=True, check=False,
        )
        if runtime_check.returncode:
            if args.offline:
                parser.error('В .venv нет средств выполнения Jupyter. Сначала запустите скрипт без --offline.')
            print('Устанавливаю средства выполнения Jupyter в .venv...', flush=True)
            subprocess.run(
                [str(python), '-m', 'pip', 'install', 'jupyterlab', 'nbclient', 'nbformat', 'ipykernel'],
                cwd=ROOT, env=environment, check=True,
            )
        subprocess.run(
            [str(python), '-m', 'ipykernel', 'install', '--prefix', str(ENVIRONMENT),
             '--name', 'rosstat-local', '--display-name', 'Python (rosstat local)'],
            cwd=ROOT, env=environment, check=True,
        )
        print('Выполняю ноутбук целиком...', flush=True)
        result = subprocess.run(
            [str(python), '-c',
             'import sys; from run import execute_notebook; execute_notebook(offline=sys.argv[1] == "1")',
             '1' if args.offline else '0'],
            cwd=ROOT, env=environment, check=False,
        )
        return result.returncode
    except (OSError, subprocess.CalledProcessError) as error:
        print(f'Ошибка запуска: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nЗапуск прерван.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
