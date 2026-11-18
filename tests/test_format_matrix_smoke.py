"""
Cycle M1: дымовой тест матрицы сохранения оформления. Корпус на месте, харнесс отрабатывает на одной паре.
Пороги не проверяются. Запуск: FLASK_ENV=testing python -m pytest tests/test_format_matrix_smoke.py -q
"""
import os
import shutil
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
sys.path.insert(0, ROOT)
os.environ.setdefault('FLASK_ENV', 'testing')

# Cycle P1: инструмент измерений нужен pdfminer и soffice; на машине без них тесты пропускаются, а не падают.
pytest.importorskip('pdfminer', reason='tools/format_matrix.py нужен pdfminer.six (см. requirements-dev.txt)')
if shutil.which('soffice') is None:
    pytest.skip('нет программы soffice (LibreOffice): матрица рендерит файлы через неё', allow_module_level=True)

import format_matrix as fm

DATA = os.path.join(ROOT, 'tests', 'data', 'matrix')


def test_corpus_is_in_place_and_small():
    total = 0
    for doc in fm.DOCS:
        for ext in fm.INPUTS:
            path = os.path.join(DATA, '%s.%s' % (doc, ext))
            assert os.path.isfile(path) and os.path.getsize(path) > 100, path
            total += os.path.getsize(path)
    assert total < 3 * 1024 * 1024
    assert os.path.isfile(os.path.join(DATA, 'README.md'))


def test_reference_profile_of_designer():
    R = fm.reference_profile('designer')
    assert R['name_centered'] and R['facts']['headings'] and R['facts']['tables'] > 0 and R['pages'] >= 1


def test_creative_texts_exist_for_all_docs():
    for doc in fm.DOCS:
        assert len(fm.creative_text(doc).strip()) > 100


def test_harness_runs_one_pair_creative_docx_to_pdf():
    client = fm.make_client()
    res = fm.run_pair(client, 'designer', 'docx', 'pdf', 'creative', tempfile.mkdtemp())
    assert 'error' not in res
    assert set(res) == set(fm.METRICS)
    assert all(v[0] in (True, False, None) and isinstance(v[1], str) for v in res.values())


def test_harness_precise_generate_for_text_input():
    st = fm.precise_generate(fm.make_client(), 'ua', 'txt')
    assert 'error' not in st and st['item_ids'] and '###ITEM_' in st['improved_resume']
