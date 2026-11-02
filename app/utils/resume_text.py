# app/utils/resume_text.py
"""
Единый извлекатель текста резюме из загруженного файла (Cycle S2).

Чистые функции, без Flask: можно вызывать и из маршрутов, и из конвейера
улучшения, и из тестов. Раньше эта логика жила внутри
_extract_text_from_request (app/__init__.py), а конвейер улучшения для не-DOCX
файлов просто делал bytes.decode("utf-8"), что для ODT и PDF давало двоичный
мусор.

Публичный интерфейс:
    extract_resume_text(filename, raw_bytes) -> str   (ValueError при ошибке)
    check_zip_safe(raw_bytes)                          (ValueError для zip-бомб)

Тексты ошибок ValueError сохранены такими же, как были в
_extract_text_from_request: на них опираются фронтенд и существующие тесты.
"""
import io
import logging
import re
import zipfile

logger = logging.getLogger(__name__)

# Защита от zip-бомб для DOCX и ODT (оба формата это zip-архивы).
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000

ERR_TOO_LARGE = "File is too large or malformed."
ERR_DOCX = "Cannot read DOCX file. Please check the file is not corrupted."
ERR_ODT = "Cannot read ODT file. Please check the file is not corrupted."
ERR_PDF = "Cannot read PDF. Please paste text manually."
ERR_RTF = "Cannot read RTF file. Please check the file is not corrupted."
ERR_RTF_EMPTY = "The RTF file contains no readable text."


# ---------------------------------------------------------------------------
# Защита от zip-бомб
# ---------------------------------------------------------------------------

def check_zip_safe(raw_bytes):
    """
    Проверить zip-контейнер (DOCX/ODT) ДО разбора: суммарный размер
    распакованных файлов не больше 50 МБ и не больше 2000 записей.
    Нарушение -> ValueError(ERR_TOO_LARGE).

    Если байты вообще не zip, здесь ничего не поднимаем: нормальный разбор
    сам выдаст привычную ошибку "Cannot read DOCX/ODT file...".
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
    except zipfile.BadZipFile:
        return
    except Exception:
        return
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise ValueError(ERR_TOO_LARGE)
        total = 0
        for info in infos:
            total += info.file_size
            if total > MAX_UNPACKED_BYTES:
                raise ValueError(ERR_TOO_LARGE)


# ---------------------------------------------------------------------------
# DOCX (логика перенесена один в один из _extract_text_from_request)
# ---------------------------------------------------------------------------

def _docx_text(raw_bytes):
    from docx import Document
    doc = Document(io.BytesIO(raw_bytes))
    parts = []
    # Читать параграфы
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text)
    # Читать таблицы (резюме часто хранятся в таблицах!)
    for table in doc.tables:
        for row in table.rows:
            row_texts = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            # убрать дубликаты ячеек
            seen = []
            for t in row_texts:
                if t not in seen:
                    seen.append(t)
            if seen:
                parts.append(' | '.join(seen))
    return '\n'.join(parts).strip()


# ---------------------------------------------------------------------------
# ODT: обход документа В ПОРЯДКЕ ДОКУМЕНТА
# ---------------------------------------------------------------------------

def _odt_ns():
    from odf.namespaces import TEXTNS, TABLENS, DRAWNS
    return TEXTNS, TABLENS, DRAWNS


def _odt_inline_text(el, frames):
    """
    Текст внутри абзаца/заголовка с учётом пробелов, табуляций и переносов
    (как teletype.extractText), НО текстовые рамки (draw:frame), якорем
    которых служит этот абзац, не вставляются в строку, а собираются в
    frames: их содержимое читается отдельно и ровно один раз.
    """
    from odf.element import Node
    TEXTNS, _TABLENS, DRAWNS = _odt_ns()
    buf = []
    for child in el.childNodes:
        if child.nodeType == Node.TEXT_NODE:
            buf.append(child.data)
        elif child.nodeType == Node.ELEMENT_NODE:
            qn = child.qname
            if qn == (TEXTNS, 'line-break'):
                buf.append('\n')
            elif qn == (TEXTNS, 'tab'):
                buf.append('\t')
            elif qn == (TEXTNS, 's'):
                c = child.getAttribute('c')
                buf.append(' ' * (int(c) if c else 1))
            elif qn == (DRAWNS, 'frame'):
                frames.append(child)
            else:
                buf.append(_odt_inline_text(child, frames))
    return ''.join(buf)


def _odt_walk(node, out):
    """
    Рекурсивно обойти детей node и дописать в out строки текста по порядку:
    text:p / text:h; text:list -> text:list-item (с вложенными списками);
    text:section; table:table; draw:frame -> draw:text-box.
    """
    TEXTNS, TABLENS, DRAWNS = _odt_ns()
    for child in node.childNodes:
        qn = getattr(child, 'qname', None)
        if qn is None:
            continue
        if qn in ((TEXTNS, 'p'), (TEXTNS, 'h')):
            frames = []
            txt = _odt_inline_text(child, frames).strip()
            if txt:
                out.append(txt)
            for fr in frames:
                _odt_walk(fr, out)
        elif qn == (TABLENS, 'table'):
            _odt_table(child, out)
        elif qn in (
            (TEXTNS, 'list'), (TEXTNS, 'list-item'), (TEXTNS, 'list-header'),
            (TEXTNS, 'section'), (DRAWNS, 'frame'), (DRAWNS, 'text-box'),
        ):
            _odt_walk(child, out)


def _odt_table(table, out):
    """Строки таблицы: ячейки через ' | ' без дубликатов (как для DOCX)."""
    _TEXTNS, TABLENS, _DRAWNS = _odt_ns()
    for child in table.childNodes:
        qn = getattr(child, 'qname', None)
        if qn == (TABLENS, 'table-row'):
            line = _odt_row(child)
            if line:
                out.append(line)
        elif qn in (
            (TABLENS, 'table-header-rows'), (TABLENS, 'table-row-group'),
            (TABLENS, 'table-rows'),
        ):
            _odt_table(child, out)


def _odt_row(row):
    _TEXTNS, TABLENS, _DRAWNS = _odt_ns()
    seen = []
    for cell in row.childNodes:
        if getattr(cell, 'qname', None) != (TABLENS, 'table-cell'):
            continue  # covered-table-cell (объединённые ячейки) пропускаем
        sub = []
        # вложенная таблица внутри ячейки читается тут же, один раз
        _odt_walk(cell, sub)
        t = '\n'.join(sub).strip()
        if t and t not in seen:
            seen.append(t)
    return ' | '.join(seen)


def _odt_text(raw_bytes):
    from odf.opendocument import load
    doc = load(io.BytesIO(raw_bytes))
    out = []
    _odt_walk(doc.text, out)
    return '\n'.join(out).strip()


# ---------------------------------------------------------------------------
# PDF / RTF / TXT
# ---------------------------------------------------------------------------

def _pdf_text(raw_bytes):
    import PyPDF2
    reader = PyPDF2.PdfReader(io.BytesIO(raw_bytes))
    text = '\n'.join([page.extract_text() for page in reader.pages])
    return text.strip()


# fcharset шрифта RTF -> кодовая страница Python. 0 и 1 (ANSI/default) = кодовая страница
# документа (\\ansicpgN). Символьные и неизвестные наборы не трогаем.
_RTF_CHARSET_CP = {
    128: 'cp932', 129: 'cp949', 130: 'cp1361', 134: 'cp936', 136: 'cp950',
    161: 'cp1253', 162: 'cp1254', 163: 'cp1258', 177: 'cp1255', 178: 'cp1256',
    186: 'cp1257', 204: 'cp1251', 222: 'cp874', 238: 'cp1250',
}
_RTF_TOKEN = re.compile(
    r"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z'])|([{}])|([^\\{}]+)", re.S)


def _rtf_decode_codepages(rtf):
    """
    Cycle S3: байты \\'hh в RTF кодируются кодовой страницей ШРИФТА текущего фрагмента
    (\\fcharsetN из \\fonttbl), а не одной \\ansicpgN документа. Word с русской локалью пишет
    \\ansicpg1251, а ивритский текст набирает шрифтом с \\fcharset177 (cp1255): striprtf декодировал
    всё как cp1251, и иврит превращался в кириллический "мусор". Здесь каждая последовательность
    \\'hh заменяется на \\uN-escape нужной кодировки, дальше обычный разбор striprtf.
    """
    tokens = list(_RTF_TOKEN.finditer(rtf))
    ansicpg = 1252
    deff = None
    for m in tokens[:200]:
        if m.group(1) == 'ansicpg' and m.group(2):
            ansicpg = int(m.group(2))
        elif m.group(1) == 'deff' and m.group(2):
            deff = int(m.group(2))

    # 1-й проход: таблица шрифтов  \\fN -> \\fcharsetM внутри {\\fonttbl ...}
    fonts, depth, ft_depth, cur_def = {}, 0, None, None
    for m in tokens:
        word, arg, hexv, sym, brace = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        if brace == '{':
            depth += 1
        elif brace == '}':
            if ft_depth is not None and depth == ft_depth:
                ft_depth = None
            depth -= 1
        elif word == 'fonttbl':
            ft_depth = depth
        elif ft_depth is not None and word == 'f' and arg:
            cur_def = int(arg)
        elif ft_depth is not None and word == 'fcharset' and arg and cur_def is not None:
            fonts[cur_def] = int(arg)

    def _cp_for(font):
        cs = fonts.get(font)
        if cs in _RTF_CHARSET_CP:
            return _RTF_CHARSET_CP[cs]
        return 'cp%d' % ansicpg

    # 2-й проход: замена последовательностей \\'hh
    out, stack = [], []
    f, uc, depth, ft_depth = deff, 1, 0, None
    i = 0
    n = len(tokens)
    while i < n:
        m = tokens[i]
        word, arg, hexv, brace = m.group(1), m.group(2), m.group(3), m.group(5)
        if brace == '{':
            stack.append((f, uc))
            depth += 1
        elif brace == '}':
            if ft_depth is not None and depth == ft_depth:
                ft_depth = None
            if stack:
                f, uc = stack.pop()
            depth -= 1
        elif word == 'fonttbl':
            ft_depth = depth
        elif ft_depth is None and word == 'f' and arg:
            f = int(arg)
        elif ft_depth is None and word == 'plain':
            f = deff
        elif ft_depth is None and word == 'uc' and arg:
            uc = int(arg)
        if hexv is not None and ft_depth is None:
            run = [int(hexv, 16)]
            j = i + 1
            while j < n and tokens[j].group(3) is not None:
                run.append(int(tokens[j].group(3), 16))
                j += 1
            try:
                chars = bytes(run).decode(_cp_for(f), errors='replace')
            except LookupError:
                chars = bytes(run).decode('latin-1')
            parts = []
            for ch in chars:
                cp = ord(ch)
                if cp > 0xFFFF:                      # суррогатная пара
                    cp -= 0x10000
                    for u in (0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)):
                        parts.append('\\u%d%s' % (u - 65536 if u > 32767 else u, '?' * uc))
                else:
                    parts.append('\\u%d%s' % (cp - 65536 if cp > 32767 else cp, '?' * uc))
            out.append(''.join(parts))
            i = j
            continue
        out.append(m.group(0))
        i += 1
    return ''.join(out)


def _rtf_text(raw_bytes):
    raw_rtf = raw_bytes.decode('latin-1')  # RTF это 7-bit ASCII с escape-последовательностями
    if not raw_rtf.lstrip().startswith('{\\rtf'):
        raise ValueError(ERR_RTF)
    try:
        raw_rtf = _rtf_decode_codepages(raw_rtf)   # Cycle S3: кодовая страница по шрифту
    except Exception as e:
        logger.warning("[extract_resume_text] RTF codepage pass failed, using raw text: %s", e)
    # маркеры списка (\bullet) striprtf отбрасывает: превращаем их в символ •,
    # чтобы структура списков не терялась
    raw_rtf = re.sub(r'\\bullet(?![A-Za-z])\s?', r'\\u8226? ', raw_rtf)
    try:
        from striprtf.striprtf import rtf_to_text
        text = rtf_to_text(raw_rtf, errors='ignore').strip()
    except Exception:
        raise ValueError(ERR_RTF)
    if not text:
        raise ValueError(ERR_RTF_EMPTY)
    return text


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def extract_resume_text(filename, raw_bytes):
    """
    Извлечь текст резюме из байтов файла по расширению имени.

    .docx  абзацы, затем таблицы (как раньше);
    .odt   обход в порядке документа: списки, секции, таблицы, рамки;
    .pdf   PyPDF2 (как раньше);
    .rtf   striprtf с сохранением маркеров списка (как раньше);
    прочее (в том числе .txt)  utf-8, errors="ignore".

    Ошибки: ValueError с человекочитаемым текстом (тексты прежние).
    """
    name = (filename or '').lower()
    raw = raw_bytes or b''

    if name.endswith('.docx'):
        check_zip_safe(raw)
        try:
            return _docx_text(raw)
        except Exception as e:
            logger.error("[extract_resume_text] DOCX parse failed: %s", e)
            raise ValueError(ERR_DOCX)

    if name.endswith('.odt'):
        check_zip_safe(raw)
        try:
            return _odt_text(raw)
        except Exception as e:
            logger.error("[extract_resume_text] ODT parse failed: %s", e)
            raise ValueError(ERR_ODT)

    if name.endswith('.pdf'):
        try:
            return _pdf_text(raw)
        except Exception:
            raise ValueError(ERR_PDF)

    if name.endswith('.rtf'):
        return _rtf_text(raw)

    return raw.decode('utf-8', errors='ignore').strip()
