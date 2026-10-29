# Локальная работа

Этот проект уже перенесён на Mac. Все команды выполняются из папки проекта.

```bash
.venv/bin/jupyter lab notebooks/rosstat_macro.ipynb
```

Повторный сбор из локального кеша без сети:

```bash
.venv/bin/python extract_dataset.py --offline --services-history
```

Проверка исходных восьми книг и всех листов:

```bash
.venv/bin/python local_check.py --offline
```

Первичная установка на новом компьютере:

```bash
bash run_local.sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m ipykernel install --prefix .venv --name rosstat-local --display-name "Python (rosstat local)"
.venv/bin/python extract_dataset.py --services-history
```

Нужен Python 3.12+. Подробности, состав данных и ограничения — в README.md.
