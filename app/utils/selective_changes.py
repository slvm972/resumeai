# app/utils/selective_changes.py
"""
Cycle SA: серверная логика "принять или отклонить отдельную правку".

apply_selection(improved_resume, display_text, changes, rejected) возвращает тексты, в которых
для отклонённых правок возвращён оригинал. Чистая функция: без сети, LLM и обращений к БД.

changes  - quality_report.changes: [{id (только Precise), original_text, improved_text}, ...]
rejected - индексы отклонённых правок в списке changes.

Правила:
  Precise (в improved_resume есть маркеры ###ITEM_NNN### или ###ITEM_NNN:TYPE###):
      блок с id правки заменяется на original_text, только если текущий текст блока равен improved_text
      (сравнение без учёта пробелов по краям и без учёта CRLF/LF); иначе правка unrevertable.
  Без маркеров (Creative):
      improved_text должен встречаться в тексте РОВНО один раз, тогда заменяется на original_text;
      если original_text пустой (добавленная строка), удаляется строка вместе с одним переводом строки;
      если improved_text пустой, не найден или встречается не один раз - правка unrevertable.
  display_text обрабатывается как текст без маркеров (в нём маркеров нет даже в Precise).
  Правка возвращается только если её можно вернуть в ОБОИХ текстах (display_text пустой = не проверяется),
  иначе она unrevertable и оба текста остаются без изменений по этой правке.
Ограничения: текст не больше 300 КБ, не больше 500 правок, иначе ValueError с безопасным сообщением.
"""
import re

MAX_TEXT_BYTES = 300 * 1024
MAX_CHANGES = 500

_MARKER_RE = re.compile(r"###ITEM_(\d+)(?::[A-Za-z_]+)?###")


def _norm(text):
    return (text or "").replace("\r\n", "\n")


def _eol_of(text):
    return "\r\n" if "\r\n" in (text or "") else "\n"


def _validate(improved_resume, display_text, changes, rejected):
    if not isinstance(improved_resume, str) or not improved_resume.strip():
        raise ValueError("improved_resume must be a non-empty string")
    if display_text is None:
        display_text = ""
    if not isinstance(display_text, str):
        raise ValueError("display_text must be a string")
    if len(improved_resume.encode("utf-8")) > MAX_TEXT_BYTES or len(display_text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("Text is too large (limit 300 KB)")
    if not isinstance(changes, list):
        raise ValueError("changes must be a list")
    if len(changes) > MAX_CHANGES:
        raise ValueError("Too many changes (limit 500)")
    for c in changes:
        if not isinstance(c, dict):
            raise ValueError("Each change must be an object")
        for key in ("original_text", "improved_text"):
            if not isinstance(c.get(key, ""), str):
                raise ValueError("Change texts must be strings")
        if c.get("id") is not None and not isinstance(c.get("id"), (str, int)):
            raise ValueError("Change id must be a string or a number")
    if not isinstance(rejected, list):
        raise ValueError("rejected must be a list")
    clean = []
    for r in rejected:
        if isinstance(r, bool) or not isinstance(r, int) or r < 0 or r >= len(changes):
            raise ValueError("rejected contains an invalid change index")
        if r not in clean:
            clean.append(r)
    return display_text, clean


def _revert_plain(text, improved, original):
    """Вернуть оригинал в тексте без маркеров. Возвращает новый текст или None, если нельзя."""
    if not improved or text.count(improved) != 1:
        return None
    pos = text.index(improved)
    if original:
        return text[:pos] + original + text[pos + len(improved):]
    end = pos + len(improved)
    if text[end:end + 1] == "\n":
        return text[:pos] + text[end + 1:]
    if pos > 0 and text[pos - 1] == "\n":
        return text[:pos - 1] + text[end:]
    return text[:pos] + text[end:]


def _revert_marked(text, change_id, improved, original):
    """Вернуть оригинал в блоке ###ITEM_NNN###. Возвращает новый текст или None, если нельзя."""
    if change_id is None or not improved:
        return None
    try:
        wanted = int(str(change_id).strip())
    except ValueError:
        return None
    headers = list(_MARKER_RE.finditer(text))
    hits = [i for i, m in enumerate(headers) if int(m.group(1)) == wanted]
    if len(hits) != 1:
        return None
    i = hits[0]
    start = headers[i].end()
    if text[start:start + 1] == "\n":
        start += 1
    end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
    body = text[start:end]
    if body.strip() != improved.strip():
        return None
    tail = body[len(body.rstrip()):]          # сохраняем разделитель между блоками как был
    return text[:start] + original.strip("\n") + tail + text[end:]


def apply_selection(improved_resume, display_text, changes, rejected):
    display_text, rejected = _validate(improved_resume, display_text, changes, rejected)
    eol_improved, eol_display = _eol_of(improved_resume), _eol_of(display_text)
    imp = _norm(improved_resume)
    disp = _norm(display_text)
    marked = bool(_MARKER_RE.search(imp))
    unrevertable = []

    for idx in rejected:
        c = changes[idx]
        original, improved = _norm(c.get("original_text", "")), _norm(c.get("improved_text", ""))
        if marked:
            new_imp = _revert_marked(imp, c.get("id"), improved, original)
        else:
            new_imp = _revert_plain(imp, improved, original)
        new_disp = disp
        if new_imp is not None and disp.strip():
            new_disp = _revert_plain(disp, improved, original)
        if new_imp is None or new_disp is None:
            unrevertable.append(idx)
            continue
        imp, disp = new_imp, new_disp

    if eol_improved == "\r\n":
        imp = imp.replace("\n", "\r\n")
    if eol_display == "\r\n":
        disp = disp.replace("\n", "\r\n")
    return {"improved_resume": imp, "display_text": disp, "unrevertable": sorted(unrevertable)}
