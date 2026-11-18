# Корпус матрицы сохранения оформления (цикл M1)

Файлы `<name>.<ext>` для пяти резюме: `designer`, `ua`, `engineer` (копии из `tests/data/creative_c1`),
`tabdates` (serif, цветные заголовки с линией, даты через правый таб, маркеры списка) и `hebrew_rtl`
(иврит справа налево, таблица «должность | годы», маркеры). Расширения: `docx` (исходник и эталон),
`odt`, `rtf`, `pdf` (с текстовым слоем), `txt` (UTF-8 без BOM). Все, кроме `docx`, получены из `docx` через
LibreOffice (`soffice --headless --convert-to`). Размер около 0,6 МБ.

Пересоздать (нужен soffice): `python tools/format_matrix.py --rebuild-corpus`
Запустить матрицу: `python tools/format_matrix.py` (пишет таблицы в консоль и в `matrix_report.md`).
