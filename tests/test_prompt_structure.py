# Guard: разметка запроса во внешних промптах-шаблонах.
#
# Конвенция (AGENTS.md §7): правила — в <system>, задание и данные —
# в <user> через плейсхолдеры. Данные (текст, глоссарий, фрагменты)
# НЕ должны попадать в <system>-часть шаблона.

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "General" / "prompts"

# Плейсхолдеры данных (не форматирующие {translation}/{relations_label}
# системных шаблонов wiki)
DATA_PLACEHOLDERS = [
    "{original_text}", "{translated_text}", "{ner_block}", "{chunk_text}",
    "{ner_json}", "{glossary}", "{rag_block}", "{batch_text}",
    "{errors_json}", "{female_names}", "{male_names}", "{dict_block}",
    "{rules_block}", "{fewshot_block}",
]

_SYS_RE = re.compile(r"(?ms)^[ \t]*<system>(.*?)</system>")


def _tagged_block(text: str, tag: str) -> str:
    m = re.search(rf"(?ms)^<{tag}>(.*?)^</{tag}>", text)
    assert m, f"тег <{tag}> не найден"
    return m.group(1)


def _system_body(block: str) -> str:
    return "\n".join(_SYS_RE.findall(block))


def _template_text(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def test_pipeline_prompt_data_not_in_system():
    text = _template_text("pipeline_prompt.txt")
    for tag in ("translate", "translate_lr", "redact", "polish"):
        sys_body = _system_body(_tagged_block(text, tag))
        for ph in DATA_PLACEHOLDERS:
            assert ph not in sys_body, f"<{tag}>: {ph} в <system>"


def test_ner_prompt_data_not_in_system():
    text = _template_text("ner_prompt.txt")
    for tag in ("prompt_pass1", "prompt_pass2"):
        sys_body = _system_body(_tagged_block(text, tag))
        for ph in ("{chunk_text}", "{ner_json}"):
            assert ph not in sys_body, f"<{tag}>: {ph} в <system>"


def test_ner_check_prompt_data_not_in_system():
    text = _template_text("ner_check_prompt.txt")
    for tag in ("prompt_ner_check", "prompt_rag"):
        sys_body = _system_body(_tagged_block(text, tag))
        # {fields} — переменная ПРАВИЛ (какие поля проверять в проходе),
        # законно живёт в <system>; данные — {glossary} и т.п.
        for ph in ("{glossary}", "{ner_block}", "{rag_block}"):
            assert ph not in sys_body, f"<{tag}>: {ph} в <system>"


def test_translate_check_prompt_data_not_in_system():
    text = _template_text("translate_check_prompt.txt")
    for tag in ("pass1", "pass2"):
        sys_body = _system_body(_tagged_block(text, tag))
        for ph in ("{batch_text}", "{errors_json}"):
            assert ph not in sys_body, f"<{tag}>: {ph} в <system>"


def test_translate_quality_prompt_data_not_in_system():
    text = _template_text("translate_quality_prompt.txt")
    block = _tagged_block(text, "prompt_assessment")
    sys_body = _system_body(block)
    for ph in ("{original_text}", "{translated_text}"):
        assert ph not in sys_body, f"prompt_assessment: {ph} в <system>"


def test_wiki_prompt_system_closes_and_has_no_data_blocks():
    text = _template_text("wiki_prompt.txt")
    block = _tagged_block(text, "prompt_wiki_article")
    assert _SYS_RE.search(block), "<system>-блок отсутствует"
    # данные термина/фрагменты собираются кодом в user_content —
    # плейсхолдеров данных в шаблоне быть не должно
    for ph in DATA_PLACEHOLDERS:
        assert ph not in block, f"prompt_wiki_article: {ph} в шаблоне"
