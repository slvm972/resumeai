"""
Тесты app/utils/resume_text.py и маршрутов чтения файлов (Cycle S2).
ODT и PDF генерируются в тестах (odfpy, reportlab), бинарных фикстур нет.
Без сети: requests.post и OpenRouterService.analyze_resume заменены заглушками.
Запуск: FLASK_ENV=testing python -m pytest tests/test_resume_text.py -q
"""
import io
import os
import sys
import uuid
import zipfile
from unittest.mock import patch

import pytest

sys.path.insert(0, '/home/claude/resumeai')

os.environ['FLASK_ENV'] = 'testing'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-tests-only')
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-for-tests-only')
os.environ.setdefault('GROQ_API_KEY', 'test-groq-key-not-used-mocked-out')

import importlib
import config as config_module
importlib.reload(config_module)

from app import create_app
from app.utils import resume_text as rt
from app.utils.resume_text import extract_resume_text

DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


# ---------------------------------------------------------------------------
# Генераторы файлов
# ---------------------------------------------------------------------------

def _save_odt(doc):
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _odt_list(items):
    from odf.text import List, ListItem, P
    lst = List()
    for it in items:
        li = ListItem()
        li.addElement(P(text=it))
        lst.addElement(li)
    return lst


def _odt_table(rows):
    from odf.table import Table, TableRow, TableCell
    from odf.text import P
    tbl = Table()
    for cells in rows:
        tr = TableRow()
        for c in cells:
            tc = TableCell()
            if hasattr(c, 'qname'):  # готовый узел (вложенная таблица); Table в odfpy это функция
                tc.addElement(c)
            else:
                tc.addElement(P(text=c))
            tr.addElement(tc)
        tbl.addElement(tr)
    return tbl


def _odt_frame(text):
    from odf.draw import Frame, TextBox
    from odf.text import P
    fr = Frame(width='5cm', height='2cm', anchortype='paragraph')
    tb = TextBox()
    tb.addElement(P(text=text))
    fr.addElement(tb)
    return fr


def _make_odt(*nodes):
    from odf.opendocument import OpenDocumentText
    doc = OpenDocumentText()
    for n in nodes:
        doc.text.addElement(n)
    return _save_odt(doc)


def _p(text):
    from odf.text import P
    return P(text=text)


def _h(text):
    from odf.text import H
    return H(outlinelevel=1, text=text)


def _pdf_bytes(lines):
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 780
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.showPage()
    c.save()
    return buf.getvalue()


def _docx_with_table(par_text, rows):
    from docx import Document
    d = Document()
    d.add_paragraph(par_text)
    t = d.add_table(rows=len(rows), cols=len(rows[0]))
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            t.cell(i, j).text = val
    d.add_paragraph('Closing paragraph')
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _legacy_docx_extract(raw):
    """Эталон: прежняя логика _extract_text_from_request для .docx, скопирована один в один."""
    from docx import Document
    doc = Document(io.BytesIO(raw))
    parts = []
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text)
    for table in doc.tables:
        for row in table.rows:
            row_texts = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            seen = []
            for t in row_texts:
                if t not in seen:
                    seen.append(t)
            if seen:
                parts.append(' | '.join(seen))
    return '\n'.join(parts).strip()


# ---------------------------------------------------------------------------
# ODT: ридер
# ---------------------------------------------------------------------------

def test_01_bulleted_list_all_items_present_in_order():
    raw = _make_odt(_p('Skills'), _odt_list(['Alpha item', 'Beta item', 'Gamma item']), _p('End'))
    text = extract_resume_text('cv.odt', raw)
    for w in ('Alpha item', 'Beta item', 'Gamma item'):
        assert w in text
    assert text.index('Alpha item') < text.index('Beta item') < text.index('Gamma item')
    assert text.index('Skills') < text.index('Alpha item') < text.index('End')


def test_02_nested_list():
    from odf.text import List, ListItem, P
    outer = List()
    li = ListItem()
    li.addElement(P(text='Outer one'))
    inner = _odt_list(['Inner A', 'Inner B'])
    li.addElement(inner)
    outer.addElement(li)
    li2 = ListItem()
    li2.addElement(P(text='Outer two'))
    outer.addElement(li2)
    text = extract_resume_text('cv.odt', _make_odt(outer))
    lines = text.split('\n')
    assert lines == ['Outer one', 'Inner A', 'Inner B', 'Outer two']


def test_03_text_section():
    from odf.text import Section
    sec = Section(name='Sec1')
    sec.addElement(_p('Inside section'))
    sec.addElement(_odt_list(['Section bullet']))
    text = extract_resume_text('cv.odt', _make_odt(_p('Before'), sec, _p('After')))
    assert text.split('\n') == ['Before', 'Inside section', 'Section bullet', 'After']


def test_04_table_stays_in_document_order():
    raw = _make_odt(_p('Title line'), _odt_table([['Left', 'Right']]), _p('After table'))
    text = extract_resume_text('cv.odt', raw)
    assert text.split('\n') == ['Title line', 'Left | Right', 'After table']


def test_05_nested_table_not_duplicated():
    inner = _odt_table([['InnerX', 'InnerY']])
    raw = _make_odt(_odt_table([['Outer cell', inner]]))
    text = extract_resume_text('cv.odt', raw)
    assert text.count('InnerX') == 1
    assert text.count('InnerY') == 1
    assert 'Outer cell' in text


def test_06_duplicate_cells_deduplicated():
    raw = _make_odt(_odt_table([['Same', 'Same', 'Other']]))
    assert extract_resume_text('cv.odt', raw) == 'Same | Other'


def test_07_text_frame_body_level_and_anchored_in_paragraph():
    from odf.text import P
    anchored = P(text='Para with frame')
    anchored.addElement(_odt_frame('Frame inside paragraph'))
    raw = _make_odt(_odt_frame('Frame at body'), anchored, _p('Tail'))
    text = extract_resume_text('cv.odt', raw)
    assert text.count('Frame at body') == 1
    assert text.count('Frame inside paragraph') == 1
    assert 'Para with frame' in text and 'Tail' in text


def test_08_hebrew_and_cyrillic():
    raw = _make_odt(_h('ניסיון תעסוקתי'), _odt_list(['ניהול רשת', 'Администрирование серверов']),
                    _p('Київ, Україна'))
    text = extract_resume_text('cv.odt', raw)
    for w in ('ניסיון תעסוקתי', 'ניהול רשת', 'Администрирование серверов', 'Київ, Україна'):
        assert w in text


def test_09_headings_and_whitespace_elements():
    from odf.text import P, S, Tab
    p = P()
    p.addText('A')
    p.addElement(S(c=3))
    p.addElement(Tab())
    p.addText('B')
    text = extract_resume_text('cv.odt', _make_odt(_h('Head'), p))
    assert text.split('\n')[0] == 'Head'
    assert text.split('\n')[1] == 'A   \tB'


def test_10_empty_odt_document_gives_empty_text_not_error():
    assert extract_resume_text('cv.odt', _make_odt()) == ''


def test_11_corrupted_and_empty_bytes_raise_old_message():
    for raw in (b'', b'this is not a zip', b'PK\x03\x04garbage'):
        with pytest.raises(ValueError) as ei:
            extract_resume_text('cv.odt', raw)
        assert str(ei.value) == "Cannot read ODT file. Please check the file is not corrupted."


def test_12_extension_is_case_insensitive():
    raw = _make_odt(_p('Case test'))
    assert extract_resume_text('CV.ODT', raw) == 'Case test'


# ---------------------------------------------------------------------------
# Защита от zip-бомб
# ---------------------------------------------------------------------------

def _zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.mark.parametrize('fname', ['bomb.odt', 'bomb.docx'])
def test_13_zip_bomb_by_unpacked_size(fname):
    raw = _zip_bytes([('content.xml', b'\x00' * (60 * 1024 * 1024))])
    assert len(raw) < 1024 * 1024  # сжимается в малый файл, как настоящая бомба
    with pytest.raises(ValueError) as ei:
        extract_resume_text(fname, raw)
    assert str(ei.value) == "File is too large or malformed."


@pytest.mark.parametrize('fname', ['many.odt', 'many.docx'])
def test_14_zip_with_too_many_entries(fname):
    raw = _zip_bytes([('f%d.txt' % i, b'x') for i in range(2001)])
    with pytest.raises(ValueError) as ei:
        extract_resume_text(fname, raw)
    assert str(ei.value) == "File is too large or malformed."


# ---------------------------------------------------------------------------
# DOCX / PDF / TXT / RTF
# ---------------------------------------------------------------------------

def test_15_docx_matches_legacy_result_on_two_files_with_tables():
    samples = [
        _docx_with_table('Header text', [['Skill', 'Level'], ['Python', 'Expert']]),
        _docx_with_table('Другой документ', [['Один', 'Один'], ['Два', '']]),
    ]
    for raw in samples:
        assert extract_resume_text('cv.docx', raw) == _legacy_docx_extract(raw)
    assert 'Python | Expert' in extract_resume_text('cv.docx', samples[0])


def test_16_corrupted_docx_old_message():
    with pytest.raises(ValueError) as ei:
        extract_resume_text('cv.docx', b'not a docx')
    assert str(ei.value) == "Cannot read DOCX file. Please check the file is not corrupted."


def test_17_pdf_is_readable():
    raw = _pdf_bytes(['Anna Smith', 'Python developer with Docker experience'])
    text = extract_resume_text('cv.pdf', raw)
    assert 'Anna Smith' in text and 'Docker' in text


def test_18_corrupted_pdf_old_message():
    with pytest.raises(ValueError) as ei:
        extract_resume_text('cv.pdf', b'%PDF-broken')
    assert str(ei.value) == "Cannot read PDF. Please paste text manually."


def test_19_txt_and_unknown_extension_decode_utf8():
    assert extract_resume_text('cv.txt', 'Привет, мир\n'.encode('utf-8')) == 'Привет, мир'
    assert extract_resume_text('cv.xyz', b'plain words') == 'plain words'


def test_20_rtf_read_and_errors():
    rtf = rb'{\rtf1\ansi\deff0 Hello RTF resume\par Second line}'
    text = extract_resume_text('cv.rtf', rtf)
    assert 'Hello RTF resume' in text and 'Second line' in text
    with pytest.raises(ValueError) as ei:
        extract_resume_text('cv.rtf', b'not rtf')
    assert str(ei.value) == "Cannot read RTF file. Please check the file is not corrupted."


# ---------------------------------------------------------------------------
# Маршруты
# ---------------------------------------------------------------------------

@pytest.fixture
def app_ctx():
    app = create_app('testing')
    app.config['TESTING'] = True
    app.config['GROQ_API_KEY'] = 'test-key'
    with app.app_context():
        yield app


def _login_admin(app, client):
    from app.services.auth_service import AuthService
    email = 'resume-text-%s@example.com' % uuid.uuid4().hex[:10]
    user = AuthService.register(email, 'somepassword123')['user']
    with client.session_transaction() as sess:
        sess['user_id'] = user.id
        sess['admin'] = 'admin'
    return user


class _FakeGroq:
    """Заглушка requests.post: запоминает prompt-ы, а в ответ возвращает блоки
    ###ITEM_NNN### из пользовательского промта без изменений (модель "ничего не улучшила")."""
    def __init__(self):
        self.prompts = []

    def post(self, url, headers=None, json=None, timeout=None):
        user_prompt = ''
        for m in (json or {}).get('messages', []):
            self.prompts.append(m.get('content', ''))
            if m.get('role') == 'user':
                user_prompt = m.get('content', '')
        start = user_prompt.find('###ITEM_')
        end = user_prompt.rfind('OUTPUT (')
        echoed = user_prompt[start:end].strip() if start >= 0 else 'no blocks'

        class _Resp:
            status_code = 200
            text = ''

            def json(self_inner):
                return {'choices': [{'message': {'content': echoed}}], 'usage': {'total_tokens': 1}}
        return _Resp()


def _file_post(client, url, name, content, **extra):
    data = {'file': (io.BytesIO(content), name)}
    data.update(extra)
    return client.post(url, data=data, content_type='multipart/form-data')


_ODT_WORDS = ['Network administrator responsible for support', 'Installed hardware for office networks',
              'Maintained servers and user accounts']


def _odt_resume():
    return _make_odt(_p('Slava Test'), _p('slava@example.com +972 50 123 4567'), _h('Experience'),
                     _odt_list(_ODT_WORDS))


def _assert_clean_prompts(fake, words):
    joined = '\n'.join(fake.prompts)
    assert joined, 'модель не вызывалась'
    assert not joined.lstrip().startswith('PK')
    assert '\x00' not in joined
    assert '\ufffd' not in joined
    for w in words:
        assert w in joined


def test_21_analyze_with_odt_contains_list_items(app_ctx):
    client = app_ctx.test_client()
    _login_admin(app_ctx, client)
    fake_result = {'success': True, 'analysis': 'ok', 'overall_score': 70, 'summary': 's'}
    with patch('app.services.openrouter_service.OpenRouterService.analyze_resume',
               return_value=fake_result) as an:
        resp = _file_post(client, '/api/analyze', 'cv.odt', _odt_resume())
    assert resp.status_code == 200, resp.get_data(as_text=True)
    sent_text = an.call_args[0][1]
    for w in _ODT_WORDS:
        assert w in sent_text
    assert resp.get_json()['resume_text'] == sent_text


def test_22_improve_odt_no_binary_garbage_and_no_docx_token(app_ctx):
    client = app_ctx.test_client()
    _login_admin(app_ctx, client)
    with client.session_transaction() as sess:  # остатки прошлого запроса не должны пережить
        sess['original_docx_token'] = 'stale-token'
        sess['item_ids'] = ['001']
    fake = _FakeGroq()
    with patch('requests.post', fake.post):
        resp = _file_post(client, '/api/improve', 'cv.odt', _odt_resume())
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body['has_original_docx'] is False
    _assert_clean_prompts(fake, _ODT_WORDS)
    with client.session_transaction() as sess:
        assert 'original_docx_token' not in sess
        assert 'item_ids' not in sess
    # экспорт текста без оригинала: 200 и настоящий DOCX, а не 500
    exp = client.post('/api/improve/docx', data={'improved_resume': body['improved_resume']})
    assert exp.status_code == 200, exp.get_data(as_text=True)
    assert exp.mimetype == DOCX_MIME
    assert exp.get_data()[:2] == b'PK'


def test_23_improve_pdf_no_binary_garbage_and_no_docx_token(app_ctx):
    client = app_ctx.test_client()
    _login_admin(app_ctx, client)
    pdf = _pdf_bytes(['Slava Test', 'slava@example.com', 'Experience',
                      'Network administrator responsible for support of offices'])
    fake = _FakeGroq()
    with patch('requests.post', fake.post):
        resp = _file_post(client, '/api/improve', 'cv.pdf', pdf)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body['has_original_docx'] is False
    _assert_clean_prompts(fake, ['Network administrator responsible for support'])
    with client.session_transaction() as sess:
        assert 'original_docx_token' not in sess
    exp = client.post('/api/improve/docx', data={'improved_resume': body['improved_resume']})
    assert exp.status_code == 200 and exp.mimetype == DOCX_MIME


def test_24_improve_docx_keeps_original_and_token(app_ctx):
    client = app_ctx.test_client()
    _login_admin(app_ctx, client)
    raw = _docx_with_table('Slava Test\nslava@example.com',
                           [['Network administrator responsible for support', 'Kyiv']])
    fake = _FakeGroq()
    with patch('requests.post', fake.post):
        resp = _file_post(client, '/api/improve', 'cv.docx', raw)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()['has_original_docx'] is True
    with client.session_transaction() as sess:
        assert sess.get('original_docx_token')


def test_25_admin_improve_odt_clean_and_no_session_token(app_ctx):
    client = app_ctx.test_client()
    _login_admin(app_ctx, client)
    with client.session_transaction() as sess:
        sess['original_docx_token'] = 'stale-token'
    fake = _FakeGroq()
    with patch('requests.post', fake.post):
        resp = _file_post(client, '/api/admin/improve', 'cv.odt', _odt_resume())
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()['has_original_docx'] is False
    _assert_clean_prompts(fake, _ODT_WORDS)
    with client.session_transaction() as sess:
        assert 'original_docx_token' not in sess


def test_26_improve_corrupted_odt_returns_400_and_does_not_call_model(app_ctx):
    client = app_ctx.test_client()
    _login_admin(app_ctx, client)
    fake = _FakeGroq()
    with patch('requests.post', fake.post):
        resp = _file_post(client, '/api/improve', 'cv.odt', b'not an odt at all, just bytes' * 3)
    assert resp.status_code == 400
    assert resp.get_json()['error'] == "Cannot read ODT file. Please check the file is not corrupted."
    assert fake.prompts == []


def test_27_improve_docx_zip_bomb_rejected_before_parsing(app_ctx):
    client = app_ctx.test_client()
    _login_admin(app_ctx, client)
    raw = _zip_bytes([('word/document.xml', b'\x00' * (60 * 1024 * 1024))])
    fake = _FakeGroq()
    with patch('requests.post', fake.post):
        resp = _file_post(client, '/api/improve', 'cv.docx', raw)
    assert resp.status_code == 400
    assert resp.get_json()['error'] == "File is too large or malformed."
    assert fake.prompts == []


# ---------------------------------------------------------------------------
# Лимит размера загрузки
# ---------------------------------------------------------------------------

def test_28_config_limit_default_is_6_mb(app_ctx):
    assert app_ctx.config['MAX_CONTENT_LENGTH'] == 6 * 1024 * 1024


@pytest.mark.parametrize('url', ['/api/analyze', '/api/improve', '/api/admin/improve'])
def test_29_upload_over_6mb_gives_413_json(app_ctx, url):
    client = app_ctx.test_client()
    _login_admin(app_ctx, client)
    big = b'x' * (7 * 1024 * 1024)
    resp = _file_post(client, url, 'big.txt', big)
    assert resp.status_code == 413
    assert resp.get_json() == {'success': False, 'error': 'File is too large (max 5 MB).'}


def test_30_small_json_routes_and_webhook_not_affected(app_ctx):
    client = app_ctx.test_client()
    _login_admin(app_ctx, client)
    # экспорт: JSON с текстом (малое тело)
    resp = client.post('/api/improve/rtf', json={'improved_resume': 'Line one\nLine two'})
    assert resp.status_code != 413
    # вебхук Lemon Squeezy: малое тело доходит до обработчика (подпись неверна, но не 413)
    wh = client.post('/webhooks/lemonsqueezy', data=b'{"meta": {}}',
                     headers={'Content-Type': 'application/json', 'X-Signature': 'bad'})
    assert wh.status_code != 413
