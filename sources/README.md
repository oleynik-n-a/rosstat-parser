# Источники Росстата

- `macro_catalog.csv` — 260 ссылок из пяти сохранённых HTML.
- `macro_selected.csv` — 8 основных файлов, 7 дополнительных и 56 архивных выпусков услуг.
- `macro_selection.json` — обоснование отбора и контрольные заголовки карточек.
- `macro_pages.json`, `macro_provenance.json` — URL страниц и SHA-256 исходных HTML.
- `extraction_specs.json` — разметка проверенных реальных Excel.
- `macro_downloads.md` — прямые ссылки и исходный план отбора до чтения Excel.
- `rosstat_11186_*.csv` — дополнительный каталог структурных показателей.
- `rosstat_sections.csv`, `rosstat_10705_provenance.json` — список 33 разделов из исходного HTML.

Исходные HTML и Excel находятся локально в `data/` и исключены из Git.
Для воспроизведения каталога: `.venv/bin/python prepare_sources.py`.

После чтения Excel установлено: `IFO_rozn_2025.xls` годовой; месячная зарплата
в рублях извлекается с 1998 года; месячный оборот розницы — номинальный и только
за 2026 год. Фактические диапазоны и примечания находятся в каждом результате
сбора, в `quality_report.csv` и `source_assessment.json`.
