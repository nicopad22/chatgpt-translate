"""
ooxml_translate.py — Unified OOXML document translator.

Translates .docx, .pptx, and .xlsx files by directly manipulating
the XML inside the zip archive.  Uses only Python stdlib — no
python-docx, openpyxl, or python-pptx.

Strategy:
  • docx / pptx — paragraph-run model.  Finds <w:p> / <a:p> elements
    across every XML file in the zip, strips run-level formatting
    (<*:rPr>), sends the clean XML to the LLM, then re-inserts the
    translated text back into the original (formatting-preserved)
    paragraph.
  • xlsx — separate mode.  Translates <si> shared strings in
    sharedStrings.xml, <is> inline strings in worksheets, and sheet
    names in workbook.xml.

Fallback: if the LLM returns malformed XML or a mismatched run count,
the paragraph is collapsed to a single unstyled run with the plain-text
translation (semantic accuracy over styling).
"""

import copy
import logging
import os
import re
import xml.etree.ElementTree as ET
import zipfile

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# OOXML Namespace Constants
# ═══════════════════════════════════════════════════════════════════════

WML = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DML = "http://schemas.openxmlformats.org/drawingml/2006/main"
SML = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _w(tag):
    return f"{{{WML}}}{tag}"


def _a(tag):
    return f"{{{DML}}}{tag}"


def _s(tag):
    return f"{{{SML}}}{tag}"


# Pre-register common OOXML prefixes so ET serialization preserves them.
_NS_MAP = {
    "w":    WML,
    "a":    DML,
    "r":    "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "mc":   "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wp":   "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "wp14": "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing",
    "w14":  "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15":  "http://schemas.microsoft.com/office/word/2012/wordml",
    "w16se": "http://schemas.microsoft.com/office/word/2015/wordml/symex",
    "wps":  "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "wpc":  "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas",
    "v":    "urn:schemas-microsoft-com:vml",
    "o":    "urn:schemas-microsoft-com:office:office",
    "m":    "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "p":    "http://schemas.openxmlformats.org/presentationml/2006/main",
    "p14":  "http://schemas.microsoft.com/office/powerpoint/2010/main",
    "dgm":  "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "c":    "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "pic":  "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "xdr":  "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a14":  "http://schemas.microsoft.com/office/drawing/2010/main",
    "w10":  "urn:schemas-microsoft-com:office:word",
}
for _pfx, _uri in _NS_MAP.items():
    ET.register_namespace(_pfx, _uri)

# ═══════════════════════════════════════════════════════════════════════
# Tag Configuration
# ═══════════════════════════════════════════════════════════════════════


class _Cfg:
    """Namespace-specific tag names for translatable element structures."""
    __slots__ = ("run", "rPr", "text", "pPr")

    def __init__(self, run, rPr, text, pPr=None):
        self.run = run
        self.rPr = rPr
        self.text = text
        self.pPr = pPr


DOCX = _Cfg(_w("r"), _w("rPr"), _w("t"), _w("pPr"))
PPTX = _Cfg(_a("r"), _a("rPr"), _a("t"), _a("pPr"))
XLSX = _Cfg(_s("r"), _s("rPr"), _s("t"))

# Paragraph tag → config lookup (for docx/pptx scanning)
_PARA_MAP = {_w("p"): DOCX, _a("p"): PPTX}

# Elements that may contain nested paragraphs / embedded content.
# _collect_text_nodes will NOT recurse into these, ensuring that text
# from text-boxes, shapes, and drawings is handled as its own paragraph.
_STOP_TAGS = {_w("p"), _a("p"), _w("drawing"), _w("pict"), _w("object")}

# ═══════════════════════════════════════════════════════════════════════
# System Prompts
# ═══════════════════════════════════════════════════════════════════════

_XML_PROMPT = (
    "You are a translation assistant working with OOXML (Office Open XML) "
    "document fragments. You will receive one or more XML fragments separated "
    'by the delimiter "---".\n\n'
    "Rules:\n"
    "1. Translate ALL human-readable text to {lang}.\n"
    "2. Preserve the XML structure EXACTLY — same tags, same attributes, "
    "same number of elements, same nesting order.\n"
    "3. Do NOT add, remove, merge, or split any XML elements or runs.\n"
    "4. Only modify the text content inside text tags.\n"
    '5. Return ONLY the translated XML fragments separated by "---", '
    "with no additional commentary or markdown formatting."
)

_PLAIN_PROMPT = (
    "You are a translation assistant. Translate the following text to {lang}. "
    "Return ONLY the translated text, with no additional commentary."
)

# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════


def translate_file(input_path, output_path, language, llm_call, on_progress=None):
    """
    Translate an OOXML file.

    Args:
        input_path:  Path to input .docx, .pptx, or .xlsx file.
        output_path: Path for the translated output file.
        language:    Target language name (e.g. "spanish", "english").
        llm_call:    Callable(system_prompt: str, user_prompt: str) -> str.
        on_progress: Optional callable(words_translated: int) called after
                     each translated element.
    """
    ext = os.path.splitext(input_path)[1].lower()
    if ext in (".docx", ".pptx"):
        _translate_paragraph_file(input_path, output_path, language, llm_call, on_progress)
    elif ext == ".xlsx":
        _translate_excel_file(input_path, output_path, language, llm_call, on_progress)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def get_word_count(filepath):
    """Count translatable words in an OOXML file."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext in (".docx", ".pptx"):
        return _count_paragraph_file(filepath)
    elif ext == ".xlsx":
        return _count_excel_file(filepath)
    return 0


# ═══════════════════════════════════════════════════════════════════════
# Text-Node Collection
# ═══════════════════════════════════════════════════════════════════════


def _collect_text_nodes(element, cfg):
    """
    Return <*:t> Element nodes belonging to *this* element's own text
    content, in document order.

    • Direct <t> children       → collected (simple Excel strings)
    • <t> inside <r> children   → collected (runs in paragraphs / rich strings)
    • Wrapper children (hyperlinks, smart-tags, …) → recursed into
    • Nested paragraphs / drawings → SKIPPED (handled as their own units)
    """
    result = []

    def _walk(el):
        for child in el:
            if child.tag == cfg.text:
                # Direct text child (e.g. simple <si><t>…</t></si>)
                result.append(child)
            elif child.tag == cfg.run:
                # Run — grab its immediate <t> children
                result.extend(child.findall(cfg.text))
            elif child.tag in _STOP_TAGS:
                pass  # nested paragraph or embedded content — skip
            else:
                # Wrapper (hyperlink, smart-tag, …) — recurse
                _walk(child)

    _walk(element)
    return result


def _get_text(element, cfg):
    """Concatenate all text from an element's own text nodes."""
    return "".join(t.text or "" for t in _collect_text_nodes(element, cfg))


# ═══════════════════════════════════════════════════════════════════════
# Stripping & Reinsertion
# ═══════════════════════════════════════════════════════════════════════


def _build_stripped(element, cfg):
    """
    Build a minimal copy of *element* containing only runs and text nodes
    (no formatting, no attributes, no non-text content).

    The structure mirrors the original closely enough for the LLM to
    return a 1-to-1 mapping of text nodes.
    """
    text_nodes = _collect_text_nodes(element, cfg)
    if not text_nodes:
        return None

    new = ET.Element(element.tag)
    has_direct_text = any(ch.tag == cfg.text for ch in element)

    for tn in text_nodes:
        if has_direct_text:
            t = ET.SubElement(new, cfg.text)
        else:
            run = ET.SubElement(new, cfg.run)
            t = ET.SubElement(run, cfg.text)
        t.text = tn.text
        sp = tn.get(XML_SPACE)
        if sp:
            t.set(XML_SPACE, sp)

    return new


def _reinsert(original, translated_xml, cfg):
    """
    Map translated text back into the original element's text nodes.

    Returns True on success, False if the LLM response can't be matched
    (triggers fallback).
    """
    try:
        trans_el = ET.fromstring(translated_xml)
    except ET.ParseError:
        return False

    orig_nodes = _collect_text_nodes(original, cfg)
    trans_nodes = _collect_text_nodes(trans_el, cfg)

    if len(orig_nodes) != len(trans_nodes):
        return False

    for o_node, t_node in zip(orig_nodes, trans_nodes):
        o_node.text = t_node.text

    return True


def _fallback(element, language, llm_call, cfg):
    """
    Fallback: destroy all run styling, translate as plain text,
    collapse to a single unstyled run (or direct <t> for simple strings).
    Semantic accuracy is preserved; formatting is lost for this element.
    """
    text = _get_text(element, cfg)
    if not text.strip():
        return

    # Remember whether the original used direct <t> (simple Excel string)
    had_direct_text = any(ch.tag == cfg.text for ch in element)

    prompt = _PLAIN_PROMPT.format(lang=language)
    translated = llm_call(prompt, text)

    # Clear all children
    for ch in list(element):
        element.remove(ch)

    if had_direct_text:
        t = ET.SubElement(element, cfg.text)
        t.text = translated.strip()
        t.set(XML_SPACE, "preserve")
    else:
        run = ET.SubElement(element, cfg.run)
        t = ET.SubElement(run, cfg.text)
        t.text = translated.strip()
        t.set(XML_SPACE, "preserve")


# ═══════════════════════════════════════════════════════════════════════
# Batching
# ═══════════════════════════════════════════════════════════════════════

_BATCH_CHAR_LIMIT = 2000  # max combined XML chars per batch


def _translate_elements(items, language, llm_call, on_progress):
    """
    Translate a list of (element, cfg) tuples, batching XML fragments
    to reduce API calls while keeping prompts short.
    """
    sys_prompt = _XML_PROMPT.format(lang=language)

    batch = []  # list of (element, stripped_xml_str, cfg)
    chars = 0

    for el, cfg in items:
        stripped = _build_stripped(el, cfg)
        if stripped is None:
            continue
        s = ET.tostring(stripped, encoding="unicode")

        if batch and chars + len(s) > _BATCH_CHAR_LIMIT:
            _send_batch(batch, language, sys_prompt, llm_call, on_progress)
            batch = []
            chars = 0

        batch.append((el, s, cfg))
        chars += len(s)

    if batch:
        _send_batch(batch, language, sys_prompt, llm_call, on_progress)


def _send_batch(batch, language, sys_prompt, llm_call, on_progress):
    """Send a batch of stripped XML fragments to the LLM."""
    user_prompt = "\n---\n".join(s for _, s, _ in batch)

    try:
        response = llm_call(sys_prompt, user_prompt)
    except Exception as exc:
        log.error("LLM batch call failed: %s", exc)
        for el, _, cfg in batch:
            _fallback(el, language, llm_call, cfg)
            _progress(el, cfg, on_progress)
        return

    parts = [p.strip() for p in response.split("---")]

    if len(parts) != len(batch):
        log.warning(
            "Batch fragment count mismatch (got %d, expected %d) — retrying individually",
            len(parts), len(batch),
        )
        _retry_individually(batch, language, sys_prompt, llm_call, on_progress)
        return

    for (el, _, cfg), translated_xml in zip(batch, parts):
        if not _reinsert(el, translated_xml, cfg):
            _fallback(el, language, llm_call, cfg)
        _progress(el, cfg, on_progress)


def _retry_individually(batch, language, sys_prompt, llm_call, on_progress):
    """Re-translate each item in the batch one at a time."""
    for el, stripped_str, cfg in batch:
        try:
            resp = llm_call(sys_prompt, stripped_str)
            if not _reinsert(el, resp.strip(), cfg):
                _fallback(el, language, llm_call, cfg)
        except Exception:
            _fallback(el, language, llm_call, cfg)
        _progress(el, cfg, on_progress)


def _progress(el, cfg, on_progress):
    if on_progress:
        on_progress(len(_get_text(el, cfg).split()))


# ═══════════════════════════════════════════════════════════════════════
# XML / Zip Utilities
# ═══════════════════════════════════════════════════════════════════════


def _register_ns(xml_bytes):
    """
    Scan raw XML bytes for namespace declarations and register them with
    ElementTree so that serialization preserves the original prefixes.
    """
    try:
        header = xml_bytes[:4000].decode("utf-8", errors="replace")
    except Exception:
        return
    # Named namespaces: xmlns:prefix="uri"
    for m in re.finditer(r'xmlns:(\w+)=["\']([^"\']+)["\']', header):
        try:
            ET.register_namespace(m.group(1), m.group(2))
        except ValueError:
            pass
    # Default namespace: xmlns="uri"
    for m in re.finditer(r'\bxmlns=["\']([^"\']+)["\']', header):
        try:
            ET.register_namespace("", m.group(1))
        except ValueError:
            pass


def _restore_namespaces(original_xml_bytes, serialized_xml_str):
    """
    ElementTree drops unused namespace definitions during serialization.
    This helper extracts any xmlns definitions from the original root tag
    and restores them onto the serialized root tag to prevent 'unbound prefix'
    errors (e.g. in mc:Ignorable).
    """
    try:
        orig_head = original_xml_bytes[:8000].decode("utf-8", errors="replace")
    except Exception:
        return serialized_xml_str

    pos = 0
    if orig_head.startswith("<?xml"):
        end_xml_dec = orig_head.find("?>")
        if end_xml_dec != -1:
            pos = end_xml_dec + 2
            
    root_start = orig_head.find("<", pos)
    if root_start == -1:
        return serialized_xml_str
        
    root_end = orig_head.find(">", root_start)
    if root_end == -1:
        return serialized_xml_str
        
    root_tag_content = orig_head[root_start:root_end]
    
    xmlns_attrs = re.findall(r'\b(xmlns(?::\w+)?\s*=\s*["\'][^"\']+["\'])', root_tag_content)
    if not xmlns_attrs:
        return serialized_xml_str
        
    ser_pos = 0
    if serialized_xml_str.startswith("<?xml"):
        end_ser_xml_dec = serialized_xml_str.find("?>")
        if end_ser_xml_dec != -1:
            ser_pos = end_ser_xml_dec + 2
            
    ser_root_start = serialized_xml_str.find("<", ser_pos)
    if ser_root_start == -1:
        return serialized_xml_str
        
    ser_root_end = serialized_xml_str.find(">", ser_root_start)
    if ser_root_end == -1:
        return serialized_xml_str
        
    ser_root_tag_content = serialized_xml_str[ser_root_start:ser_root_end]
    
    to_add = []
    for attr in xmlns_attrs:
        name_match = re.match(r'^(xmlns(?::\w+)?)\s*=', attr)
        if name_match:
            name = name_match.group(1)
            if not re.search(r'\b' + re.escape(name) + r'\s*=', ser_root_tag_content):
                to_add.append(attr)
                
    if to_add:
        is_self_closing = ser_root_tag_content.endswith("/")
        insert_pos = ser_root_end
        if is_self_closing:
            insert_pos = ser_root_end - 1
            
        added_str = " " + " ".join(to_add)
        serialized_xml_str = serialized_xml_str[:insert_pos] + added_str + serialized_xml_str[insert_pos:]
        
    return serialized_xml_str


def _serialize(root, original_bytes=None):
    """Serialize an XML element tree to bytes with an OOXML-style declaration."""
    body = ET.tostring(root, encoding="unicode")
    if original_bytes:
        body = _restore_namespaces(original_bytes, body)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + body
    ).encode("utf-8")


def _write_zip(input_path, output_path, modified_files):
    """
    Write an output zip by copying the input zip and replacing any files
    whose content was modified.  Non-XML entries (images, media, etc.)
    are copied byte-for-byte.
    """
    with zipfile.ZipFile(input_path, "r") as zin:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                if info.filename in modified_files:
                    zout.writestr(info, modified_files[info.filename])
                else:
                    zout.writestr(info, zin.read(info.filename))


# ═══════════════════════════════════════════════════════════════════════
# Paragraph-Based Translation (docx / pptx)
# ═══════════════════════════════════════════════════════════════════════


def _translate_paragraph_file(input_path, output_path, language, llm_call, on_progress):
    """Translate a .docx or .pptx file."""
    modified = {}

    with zipfile.ZipFile(input_path, "r") as zin:
        for info in zin.infolist():
            if not info.filename.endswith(".xml"):
                continue

            raw = zin.read(info.filename)
            _register_ns(raw)

            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                continue

            # Collect translatable paragraphs across all known namespaces
            items = []
            for p_tag, cfg in _PARA_MAP.items():
                for para in root.iter(p_tag):
                    if _get_text(para, cfg).strip():
                        items.append((para, cfg))

            if not items:
                continue

            log.info("  %s: %d paragraph(s)", info.filename, len(items))
            _translate_elements(items, language, llm_call, on_progress)
            modified[info.filename] = _serialize(root, raw)

    _write_zip(input_path, output_path, modified)


# ═══════════════════════════════════════════════════════════════════════
# Excel Translation
# ═══════════════════════════════════════════════════════════════════════


def _translate_excel_file(input_path, output_path, language, llm_call, on_progress):
    """Translate a .xlsx file."""
    modified = {}
    cfg = XLSX
    plain_prompt = _PLAIN_PROMPT.format(lang=language)

    with zipfile.ZipFile(input_path, "r") as zin:
        for info in zin.infolist():
            if not info.filename.endswith(".xml"):
                continue

            raw = zin.read(info.filename)
            _register_ns(raw)

            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                continue

            dirty = False

            # ── Shared strings ─────────────────────────────────────
            if info.filename.endswith("sharedStrings.xml"):
                items = [
                    (si, cfg)
                    for si in root.iter(_s("si"))
                    if _get_text(si, cfg).strip()
                ]
                if items:
                    log.info("  sharedStrings.xml: %d string(s)", len(items))
                    _translate_elements(items, language, llm_call, on_progress)
                    dirty = True

            # ── Inline strings in worksheets ───────────────────────
            elif "worksheets/sheet" in info.filename:
                items = [
                    (is_el, cfg)
                    for is_el in root.iter(_s("is"))
                    if _get_text(is_el, cfg).strip()
                ]
                if items:
                    log.info("  %s: %d inline string(s)", info.filename, len(items))
                    _translate_elements(items, language, llm_call, on_progress)
                    dirty = True

            # ── Sheet names in workbook ────────────────────────────
            elif info.filename.endswith("workbook.xml"):
                for sheet in root.iter(_s("sheet")):
                    name = sheet.get("name")
                    if name and name.strip():
                        try:
                            translated = llm_call(plain_prompt, name)
                            sheet.set("name", translated.strip())
                            if on_progress:
                                on_progress(len(name.split()))
                        except Exception:
                            pass  # keep original name on failure
                dirty = True

            if dirty:
                modified[info.filename] = _serialize(root, raw)

    _write_zip(input_path, output_path, modified)


# ═══════════════════════════════════════════════════════════════════════
# Word Count
# ═══════════════════════════════════════════════════════════════════════


def _count_paragraph_file(filepath):
    """Count translatable words in a .docx or .pptx file."""
    words = 0
    with zipfile.ZipFile(filepath, "r") as z:
        for info in z.infolist():
            if not info.filename.endswith(".xml"):
                continue
            try:
                root = ET.fromstring(z.read(info.filename))
            except ET.ParseError:
                continue
            for p_tag, cfg in _PARA_MAP.items():
                for para in root.iter(p_tag):
                    words += len(_get_text(para, cfg).split())
    return words


def _count_excel_file(filepath):
    """Count translatable words in a .xlsx file."""
    words = 0
    cfg = XLSX
    with zipfile.ZipFile(filepath, "r") as z:
        for info in z.infolist():
            if not info.filename.endswith(".xml"):
                continue
            try:
                root = ET.fromstring(z.read(info.filename))
            except ET.ParseError:
                continue
            if info.filename.endswith("sharedStrings.xml"):
                for si in root.iter(_s("si")):
                    words += len(_get_text(si, cfg).split())
            elif "worksheets/sheet" in info.filename:
                for is_el in root.iter(_s("is")):
                    words += len(_get_text(is_el, cfg).split())
            elif info.filename.endswith("workbook.xml"):
                for sheet in root.iter(_s("sheet")):
                    name = sheet.get("name")
                    if name:
                        words += len(name.split())
    return words
