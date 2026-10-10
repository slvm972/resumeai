#!/usr/bin/env python3
"""
migrate_db.py — перенос базы ResumeAI с Render Postgres на Neon Postgres.

Что делает (по порядку):
  1. Читает строки подключения из двух текстовых файлов (пароли в чат/в код не попадают).
  2. Проверяет подключение к обеим базам.
  3. Создаёт таблицы в Neon командой `alembic upgrade head` (как на проде).
  4. Копирует все данные из Render в Neon (из Render только ЧИТАЕТ, ничего там не меняет).
  5. Выравнивает счётчики id (чтобы новые записи не конфликтовали со старыми).
  6. Сверяет число строк в каждой таблице и пишет итог.

Запуск (из папки проекта, там же где alembic.ini):
  Проверка без записи:
      python migrate_db.py --render путь\\render.txt --neon путь\\neon.txt --dry-run
  Настоящий перенос:
      python migrate_db.py --render путь\\render.txt --neon путь\\neon.txt

Файлы render.txt / neon.txt НЕ кладите в папку проекта (там пароли, можно случайно
отправить в GitHub). Держите их на рабочем столе.
"""
import argparse
import os
import re
import subprocess
import sys

try:
    from sqlalchemy import MetaData, create_engine, text
    from sqlalchemy.types import JSON
except ImportError:
    print("ОШИБКА: не установлен SQLAlchemy. Запускайте скрипт в той же консоли/окружении, "
          "где вы запускаете проект (python run.py).")
    sys.exit(1)

BATCH = 500
URL_RE = re.compile(r"[A-Za-z0-9+]+://[^\s'\"]+")


def read_url(path):
    """Достать строку подключения из текстового файла (даже если вокруг есть лишний текст)."""
    if not os.path.isfile(path):
        sys.exit(f"ОШИБКА: файл не найден: {path}")
    with open(path, encoding="utf-8-sig", errors="ignore") as f:
        content = f.read()
    m = URL_RE.search(content)
    if not m:
        sys.exit(f"ОШИБКА: в файле {path} не найдена строка подключения (postgresql://...)")
    url = m.group(0).strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def safe_name(url):
    """Адрес базы без логина и пароля — для вывода на экран."""
    m = re.search(r"@([^/?]+)/([^?]*)", url)
    return f"{m.group(1)}/{m.group(2)}" if m else "(локальная база)"


def count_rows(conn, table):
    return conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", required=True, help="файл со строкой подключения Render (источник)")
    ap.add_argument("--neon", required=True, help="файл со строкой подключения Neon (назначение)")
    ap.add_argument("--dry-run", action="store_true", help="только проверить подключение и посчитать строки")
    ap.add_argument("--skip-alembic", action="store_true", help=argparse.SUPPRESS)  # для тестов
    args = ap.parse_args()

    src_url = read_url(args.render)
    dst_url = read_url(args.neon)
    if src_url == dst_url:
        sys.exit("ОШИБКА: строки Render и Neon одинаковые. Проверьте файлы.")

    print(f"Источник (Render): {safe_name(src_url)}")
    print(f"Назначение (Neon): {safe_name(dst_url)}")
    print()

    # ---- 1. Подключение к источнику и чтение структуры ----
    try:
        src = create_engine(src_url, pool_pre_ping=True)
        with src.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as e:
        sys.exit(f"ОШИБКА: не удалось подключиться к Render.\n"
                 f"Проверьте, что в render.txt лежит External Database URL.\nДетали: {type(e).__name__}")

    src_meta = MetaData()
    src_meta.reflect(bind=src)
    tables = [t for t in src_meta.sorted_tables if t.name != "alembic_version"]

    print("В базе Render найдено:")
    with src.connect() as c:
        src_counts = {t.name: count_rows(c, t.name) for t in tables}
    for name, n in src_counts.items():
        print(f"  {name:<25} {n} строк")
    print()

    if args.dry_run:
        try:
            dst = create_engine(dst_url, pool_pre_ping=True)
            with dst.connect() as c:
                c.execute(text("SELECT 1"))
            print("Подключение к Neon: ОК")
        except Exception as e:
            sys.exit(f"ОШИБКА: не удалось подключиться к Neon.\nДетали: {type(e).__name__}")
        print("Проверка прошла. Ничего не изменено. Теперь можно запускать без --dry-run.")
        return

    # ---- 2. Структура в Neon через alembic (как на проде) ----
    if not args.skip_alembic:
        if not os.path.isfile("alembic.ini"):
            sys.exit("ОШИБКА: alembic.ini не найден. Запускайте скрипт из папки проекта (resumeai).")
        print("Создаю таблицы в Neon (alembic upgrade head)...")
        env = dict(os.environ, DATABASE_URL=dst_url)
        r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env)
        if r.returncode != 0:
            sys.exit("ОШИБКА: alembic не смог создать таблицы в Neon. Скопируйте текст ошибки выше "
                     "и пришлите мне.")
        print()

    dst = create_engine(dst_url, pool_pre_ping=True)
    dst_meta = MetaData()
    dst_meta.reflect(bind=dst)

    # Таблицы, которых нет в Neon после alembic (если они были созданы в Render другим путём)
    missing = [t for t in tables if t.name not in dst_meta.tables]
    if missing:
        print("Внимание: этих таблиц нет в миграциях, создаю по образцу Render: "
              + ", ".join(t.name for t in missing))
        src_meta.create_all(dst, tables=missing, checkfirst=True)
        dst_meta = MetaData()
        dst_meta.reflect(bind=dst)

    # ---- 3. Neon должен быть пустым ----
    with dst.connect() as c:
        non_empty = {t.name: count_rows(c, t.name) for t in tables if count_rows(c, t.name) > 0}
    if non_empty:
        sys.exit("ОШИБКА: в Neon уже есть данные: " + ", ".join(f"{k}={v}" for k, v in non_empty.items())
                 + "\nЧтобы ничего не задвоить, скрипт остановлен. Пришлите мне этот вывод.")

    # ---- 4. Копирование (одной транзакцией: либо всё, либо ничего) ----
    print("Копирую данные...")
    problems = []
    with src.connect() as sc, dst.begin() as dc:
        for t in tables:
            dt = dst_meta.tables[t.name]
            # Пустое значение (NULL) в JSON-колонках должно остаться пустым, а не
            # превратиться в JSON-слово 'null'
            for col in dt.columns:
                if isinstance(col.type, JSON):
                    col.type.none_as_null = True
            src_cols = {c.name for c in t.columns}
            dst_cols = {c.name for c in dt.columns}
            common = [c.name for c in t.columns if c.name in dst_cols]
            lost = sorted(src_cols - dst_cols)
            if lost:
                problems.append(f"{t.name}: в Neon нет колонок {lost} — данные из них не перенесены")
            required_missing = [c.name for c in dt.columns
                                if c.name not in src_cols and not c.nullable
                                and c.server_default is None and not c.primary_key]
            if required_missing:
                raise SystemExit(f"ОШИБКА: в Neon у таблицы {t.name} есть обязательные колонки "
                                 f"{required_missing}, которых нет в Render. Пришлите мне этот вывод.")
            rows = sc.execute(t.select()).mappings().all()
            data = [{k: r[k] for k in common} for r in rows]
            for i in range(0, len(data), BATCH):
                dc.execute(dt.insert(), data[i:i + BATCH])
            print(f"  {t.name:<25} {len(data)} строк")

        # ---- 5. Выравнивание счётчиков id (только для Postgres) ----
        if dst.dialect.name == "postgresql":
            for t in tables:
                pk = [c.name for c in t.primary_key.columns]
                if len(pk) != 1:
                    continue
                col = pk[0]
                dc.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('\"{t.name}\"', '{col}'), "
                    f"COALESCE(MAX(\"{col}\"), 1), MAX(\"{col}\") IS NOT NULL) FROM \"{t.name}\" "
                    f"WHERE pg_get_serial_sequence('\"{t.name}\"', '{col}') IS NOT NULL"
                ))

    # ---- 6. Проверка ----
    print()
    print("Проверка (Render -> Neon):")
    ok = True
    with dst.connect() as c:
        for t in tables:
            n_dst = count_rows(c, t.name)
            n_src = src_counts[t.name]
            mark = "OK " if n_dst == n_src else "!!!"
            if n_dst != n_src:
                ok = False
            print(f"  {mark} {t.name:<25} {n_src} -> {n_dst}")
    for p in problems:
        print("  Внимание:", p)
        ok = False
    print()
    if ok:
        print("ГОТОВО: все данные перенесены, числа строк совпали.")
    else:
        print("ЕСТЬ РАСХОЖДЕНИЯ. Не переключайте сайт на Neon. Пришлите мне этот вывод целиком.")
        sys.exit(2)


if __name__ == "__main__":
    main()
