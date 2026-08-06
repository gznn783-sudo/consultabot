from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

# Aceita tanto {{autor}} quanto o formato antigo {{autor|95|8}}.
# Os números antigos são ignorados: o bloco inteiro é refeito e refluído.
MARKER_RE = re.compile(
    r"\{\{\s*([A-Za-zÀ-ÿ0-9_]+)\s*(?:\|[^{}]*)?\}\}",
    re.UNICODE,
)

LABEL_RE = re.compile(
    r"^\s*(autor|r[eé]u|requerido|parte\s+r[eé]|cpf|assunto|valor|pend[eê]ncias|custa|nome|chave|pix|banco|"
    r"ag[eê]ncia|conta|advogado|opera[cç][aã]o|n[uú]mero|tribunal)\b",
    re.IGNORECASE,
)


class TemplateError(ValueError):
    """Erro legível para o usuário ao inspecionar ou preencher um modelo."""


@dataclass(frozen=True)
class SpanStyle:
    font_size: float
    bold: bool
    italic: bool
    family: str
    color: str


@dataclass
class EditableBlock:
    page_number: int
    erase_rect: pymupdf.Rect
    insert_rect: pymupdf.Rect
    html_text: str
    css: str
    fields: list[str]
    mode: str


def _css_family(font_name: str, flags: int) -> str:
    """Escolhe uma família visual próxima sem depender de fontes externas."""
    name = re.sub(r"[^a-z0-9]", "", font_name.lower())

    mono_hints = ("mono", "courier", "consolas", "menlo", "code", "typewriter")
    serif_hints = (
        "times",
        "georgia",
        "garamond",
        "cambria",
        "palatino",
        "bookman",
        "baskerville",
        "serif",
    )
    sans_hints = (
        "sans",
        "arial",
        "helvetica",
        "calibri",
        "aptos",
        "roboto",
        "montserrat",
        "lato",
        "verdana",
        "tahoma",
        "nimbus",
        "opensans",
        "poppins",
    )

    if any(hint in name for hint in mono_hints):
        return "monospace"
    if any(hint in name for hint in sans_hints):
        return "sans-serif"
    if any(hint in name for hint in serif_hints):
        return "serif"

    # Flags do PyMuPDF: bit 3 mono, bit 2 serif. Alguns PDFs marcam fontes
    # sans como serif; por isso os nomes acima têm prioridade.
    if flags & (1 << 3):
        return "monospace"
    if flags & (1 << 2):
        return "serif"
    return "sans-serif"


def _span_style(span: dict) -> SpanStyle:
    flags = int(span.get("flags", 0))
    font_name = str(span.get("font", ""))
    lowered = font_name.lower()
    color_value = int(span.get("color", 0))
    rgb = pymupdf.sRGB_to_rgb(color_value)
    color = "#{:02x}{:02x}{:02x}".format(*rgb)

    return SpanStyle(
        font_size=max(float(span.get("size", 10.0)), 1.0),
        bold=bool(flags & (1 << 4)) or "bold" in lowered or "black" in lowered,
        italic=bool(flags & (1 << 1)) or "italic" in lowered or "oblique" in lowered,
        family=_css_family(font_name, flags),
        color=color,
    )


def _line_text(line: dict) -> str:
    return "".join(str(span.get("text", "")) for span in line.get("spans", []))


def _block_text(block: dict, separator: str = "\n") -> str:
    return separator.join(_line_text(line) for line in block.get("lines", []))


def _marker_fields(text: str) -> list[str]:
    fields: list[str] = []
    for match in MARKER_RE.finditer(text):
        name = match.group(1).strip().lower()
        if name not in fields:
            fields.append(name)
    return fields


def _choose_mode(lines: list[str]) -> str:
    """Distingue lista com quebras reais de parágrafo com quebra apenas visual."""
    clean = [line.strip() for line in lines if line.strip()]
    if len(clean) <= 1:
        return "flow"

    label_count = sum(bool(LABEL_RE.match(line)) for line in clean)
    colon_count = sum(":" in line[:45] for line in clean)

    # Autor/CPF/Assunto/Banco etc. permanecem em linhas estruturais.
    if label_count >= 2 or colon_count >= max(2, len(clean) // 2):
        return "lines"

    if clean[0].endswith(":"):
        return "lines"

    # Em parágrafos, as quebras exportadas pelo Canva são recalculadas.
    return "flow"


def _build_char_stream(block: dict, mode: str) -> tuple[str, list[SpanStyle]]:
    chars: list[str] = []
    styles: list[SpanStyle] = []
    lines = block.get("lines", [])

    fallback = SpanStyle(10.0, False, False, "sans-serif", "#000000")
    previous_style = fallback

    for line_index, line in enumerate(lines):
        if line_index:
            separator = "\n" if mode == "lines" else " "
            chars.extend(separator)
            styles.extend([previous_style] * len(separator))

        for span in line.get("spans", []):
            text = str(span.get("text", ""))
            style = _span_style(span)
            previous_style = style
            chars.extend(text)
            styles.extend([style] * len(text))

    return "".join(chars), styles


def _replace_markers(
    source: str,
    styles: list[SpanStyle],
    values: dict[str, str],
) -> tuple[str, list[SpanStyle], list[str]]:
    out_chars: list[str] = []
    out_styles: list[SpanStyle] = []
    fields: list[str] = []
    cursor = 0

    for match in MARKER_RE.finditer(source):
        out_chars.extend(source[cursor : match.start()])
        out_styles.extend(styles[cursor : match.start()])

        field = match.group(1).strip().lower()
        if field not in fields:
            fields.append(field)
        if field not in values:
            raise TemplateError(f"Falta o campo '{field}'.")

        replacement = str(values[field])
        marker_style = (
            styles[match.start()]
            if match.start() < len(styles)
            else (styles[-1] if styles else SpanStyle(10.0, False, False, "sans-serif", "#000000"))
        )
        out_chars.extend(replacement)
        out_styles.extend([marker_style] * len(replacement))
        cursor = match.end()

    out_chars.extend(source[cursor:])
    out_styles.extend(styles[cursor:])
    return "".join(out_chars), out_styles, fields


def _style_css(style: SpanStyle) -> str:
    return ";".join(
        [
            f"font-family:{style.family}",
            f"font-size:{style.font_size:.3f}pt",
            f"font-weight:{'700' if style.bold else '400'}",
            f"font-style:{'italic' if style.italic else 'normal'}",
            f"color:{style.color}",
        ]
    )


def _to_html(text: str, styles: list[SpanStyle]) -> str:
    if not text:
        return ""

    parts: list[str] = []
    start = 0
    current = styles[0] if styles else SpanStyle(10.0, False, False, "sans-serif", "#000000")

    def emit(segment: str, style: SpanStyle) -> None:
        if not segment:
            return
        escaped = html.escape(segment).replace("\n", "<br>")
        parts.append(f'<span style="{_style_css(style)}">{escaped}</span>')

    for index in range(1, len(text)):
        style = styles[index] if index < len(styles) else current
        if style != current:
            emit(text[start:index], current)
            start = index
            current = style
    emit(text[start:], current)
    return "".join(parts)


def _infer_alignment(block: dict, mode: str) -> str:
    # Os parágrafos do modelo mostrado são justificados. Listas continuam à esquerda.
    if mode == "flow" and len(block.get("lines", [])) >= 2:
        return "justify"
    return "left"


def _line_height(block: dict) -> float:
    sizes: list[float] = []
    baselines: list[float] = []

    for line in block.get("lines", []):
        spans = line.get("spans", [])
        sizes.extend(float(span.get("size", 10.0)) for span in spans)
        origins = [span.get("origin") for span in spans if span.get("origin")]
        if origins:
            baselines.append(float(origins[0][1]))

    default_size = max(sizes) if sizes else 10.0
    if len(baselines) >= 2:
        gaps = [b - a for a, b in zip(baselines, baselines[1:]) if 0 < b - a < default_size * 3]
        if gaps:
            return max(sum(gaps) / len(gaps), default_size * 1.05)
    return default_size * 1.18


def _horizontal_overlap(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    overlap = max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))
    base = max(1.0, min(a.width, b.width))
    return overlap / base


def _safe_bottom(page: pymupdf.Page, target: pymupdf.Rect, all_blocks: list[dict]) -> float:
    candidates: list[float] = []
    for block in all_blocks:
        bbox = block.get("bbox")
        if not bbox:
            continue
        rect = pymupdf.Rect(bbox)
        if rect.y0 <= target.y1 + 1:
            continue
        if _horizontal_overlap(target, rect) >= 0.22:
            candidates.append(rect.y0)

    page_limit = page.rect.y1 - 12
    if candidates:
        return max(target.y1 + 2, min(min(candidates) - 3, page_limit))
    return page_limit


def _safe_right(page: pymupdf.Page, target: pymupdf.Rect, all_blocks: list[dict]) -> float:
    """Amplia caixas curtas até a área útil, sem atravessar uma coluna vizinha."""
    same_band_right: list[float] = []
    content_right: list[float] = []

    for block in all_blocks:
        bbox = block.get("bbox")
        if not bbox:
            continue
        rect = pymupdf.Rect(bbox)
        content_right.append(rect.x1)

        vertical_overlap = max(0.0, min(target.y1, rect.y1) - max(target.y0, rect.y0))
        if rect.x0 > target.x1 + 2 and vertical_overlap >= min(target.height, rect.height) * 0.35:
            same_band_right.append(rect.x0)

    if same_band_right:
        return max(target.x1, min(same_band_right) - 3)

    # PDFs não guardam a largura vazia da caixa do Canva. Quando não existe
    # coluna à direita, usamos o limite direito do conteúdo principal da página.
    page_content_right = max(content_right, default=page.rect.x1 - 18)
    right = max(target.x1, min(page_content_right, page.rect.x1 - 12))
    return right


def _make_editable_blocks(page: pymupdf.Page, values: dict[str, str]) -> list[EditableBlock]:
    page_dict = page.get_text("dict", sort=True, flags=pymupdf.TEXTFLAGS_TEXT)
    all_blocks = page_dict.get("blocks", [])
    edits: list[EditableBlock] = []

    for block in all_blocks:
        if block.get("type", 0) != 0 or not block.get("lines"):
            continue

        raw = _block_text(block)
        fields = _marker_fields(raw)
        if not fields:
            continue

        lines = [_line_text(line) for line in block.get("lines", [])]
        mode = _choose_mode(lines)
        source, source_styles = _build_char_stream(block, mode)
        replaced, replaced_styles, replaced_fields = _replace_markers(source, source_styles, values)

        rect = pymupdf.Rect(block["bbox"])
        erase_rect = pymupdf.Rect(rect.x0 - 0.8, rect.y0 - 0.8, rect.x1 + 0.8, rect.y1 + 0.8)
        bottom = _safe_bottom(page, rect, all_blocks)
        right = _safe_right(page, rect, all_blocks)
        insert_rect = pymupdf.Rect(
            rect.x0,
            max(page.rect.y0, rect.y0 - 0.6),
            right,
            bottom,
        )

        alignment = _infer_alignment(block, mode)
        leading = _line_height(block)
        html_text = f'<div class="pdf-block">{_to_html(replaced, replaced_styles)}</div>'
        css = (
            "html,body,div,p,span{margin:0;padding:0;}"
            ".pdf-block{"
            f"line-height:{leading:.3f}pt;"
            f"text-align:{alignment};"
            "white-space:normal;"
            "}"
        )

        edits.append(
            EditableBlock(
                page_number=page.number,
                erase_rect=erase_rect,
                insert_rect=insert_rect,
                html_text=html_text,
                css=css,
                fields=replaced_fields,
                mode=mode,
            )
        )

    return edits


def inspect_template(path: str | Path) -> dict:
    path = Path(path)
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise TemplateError(f"Não foi possível abrir o PDF: {exc}") from exc

    fields: list[str] = []
    occurrences: list[dict] = []
    try:
        for page in doc:
            page_dict = page.get_text("dict", sort=True, flags=pymupdf.TEXTFLAGS_TEXT)
            for block in page_dict.get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                found = _marker_fields(_block_text(block))
                if found:
                    occurrences.append({"pagina": page.number + 1, "campos": found})
                    for field in found:
                        if field not in fields:
                            fields.append(field)
    finally:
        doc.close()

    if not fields:
        raise TemplateError(
            "Nenhum marcador foi encontrado. Use {{autor}} ou {{autor|95|8}} e exporte como PDF padrão sem achatar."
        )

    return {"fields": fields, "occurrences": occurrences}


def fill_pdf(
    template_path: str | Path,
    output_path: str | Path,
    values: dict[str, str],
    *,
    minimum_scale: float = 0.50,
) -> list[str]:
    """Preenche os blocos com marcadores e devolve avisos de diagramação."""
    template_path = Path(template_path)
    output_path = Path(output_path)
    normalized_values = {str(k).strip().lower(): str(v) for k, v in values.items()}

    info = inspect_template(template_path)
    missing = [field for field in info["fields"] if field not in normalized_values]
    if missing:
        raise TemplateError("Faltam os campos: " + ", ".join(missing))

    doc = pymupdf.open(template_path)
    warnings: list[str] = []
    try:
        for page in doc:
            edits = _make_editable_blocks(page, normalized_values)
            if not edits:
                continue

            # Remove o texto do bloco inteiro. A cobertura é transparente:
            # imagens, fundos e desenhos do modelo continuam visíveis.
            for edit in edits:
                page.add_redact_annot(edit.erase_rect, fill=None, cross_out=False)
            page.apply_redactions(images=0, graphics=0, text=0)

            for index, edit in enumerate(edits, start=1):
                spare, scale = page.insert_htmlbox(
                    edit.insert_rect,
                    edit.html_text,
                    css=edit.css,
                    scale_low=minimum_scale,
                    overlay=True,
                )

                if spare < 0:
                    # Última tentativa: reduzir mais em vez de sobrepor conteúdo.
                    spare, scale = page.insert_htmlbox(
                        edit.insert_rect,
                        edit.html_text,
                        css=edit.css,
                        scale_low=0.30,
                        overlay=True,
                    )

                if spare < 0:
                    raise TemplateError(
                        f"O bloco {index} da página {page.number + 1} não coube. "
                        "Aumente a caixa de texto no Canva ou reduza o conteúdo."
                    )

                if scale < 0.78:
                    warnings.append(
                        f"Página {page.number + 1}: um bloco foi reduzido para {scale * 100:.0f}% para caber."
                    )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(output_path, garbage=4, deflate=True, clean=True)
    finally:
        doc.close()

    return warnings
