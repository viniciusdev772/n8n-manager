"""
Parser do Relatório de Saldo de Abastecimento (fab0257)
Extrai itens (falta), substitutos, cores e valor devido (Abast negativo).

Uso:
    python parse_falta.py <caminho_do_pdf>

Saída:
    JSON → <nome>_parsed.json
    CSV  → <nome>_parsed.csv

Estrutura de colunas no PDF:
    Item original  (falta)     : x0 ≈ 14.4  | cor em x0 ≈ 164.2
    Item substituto (importado): x0 ≈ 17.6  | cor em x0 ≈ 156.4
    Unid (M/MT)                : x0 ≈ 149
    Dados numéricos (ignorar)  : x0 ≥ 226
    Abast (valor devido)       : x0 ≈ 245–285
"""

import sys, re, json, os, tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from typing import List, Optional
from html import escape
import pdfplumber
import pandas as pd
from collections import defaultdict
try:
    from fastapi import FastAPI, File, HTTPException, Request, UploadFile
    from fastapi.responses import FileResponse
    FASTAPI_AVAILABLE = True
except ModuleNotFoundError:
    FastAPI = None
    File = None
    HTTPException = Exception
    Request = None
    UploadFile = None
    FileResponse = None
    FASTAPI_AVAILABLE = False

# ── Thresholds de coluna ─────────────────────────────────────────────────────
X_ORIGINAL_MAX   = 16.0   # item original começa em ~14.4  (x0 < 16)
X_SUBSTITUTO_MAX = 20.0   # substituto começa em ~17.6     (16 ≤ x0 < 20)
X_ITEM_DESC_MAX  = 145    # descrição do item termina antes de 145

X_COLOR_ORIG_MIN = 160    # cor do item original começa ~164
X_COLOR_SUB_MIN  = 153    # cor do substituto começa ~156
X_COLOR_MAX      = 226    # dados numéricos começam ~230 (ignorar)

X_ABAST_MIN      = 240    # coluna Abast começa ~249
X_ABAST_MAX      = 290    # coluna Abast termina antes de ~290
X_SALDO_CASA_MIN = 330    # coluna "em casa" começa ~335
X_SALDO_CASA_MAX = 370    # coluna "em casa" termina ~364

# ── Comportamento ─────────────────────────────────────────────────────────────
INCLUDE_SUBSTITUTES = False  # True = inclui importados/substitutos no output

SKIP_PATTERNS = re.compile(
    r"(Empresa:|Filial:|Usuário:|Tipo\s+Relatório|Grupo\s+de|Grupo\s+POI|"
    r"fab0257|Relatório\s+de|Página|Descrição|Unid\.Cor|Mini\s+Fabrica|"
    r"Total\s+de|CALCADOS|BEIRA\s+RIO|S/A|FILIAL|Somente\s+Falta|"
    r"\d{2}/\d{2}/\d{4}|\d{2}:\d{2}:\d{2}|Usuário:)",
    re.IGNORECASE,
)

ITEM_CODE_RE  = re.compile(r"^\d{4,6}$")
COLOR_CODE_RE = re.compile(r"^\d{1,6}$")
ABAST_RE      = re.compile(r"^-[\d,\.]+$")
NUMERIC_RE    = re.compile(r"^-?[\d,]+\.\d+$|^-?[\d,]+$")
MINI_FABRICA_RE = re.compile(r"Mini\s+Fabrica\s*-\s*(.+)$", re.IGNORECASE)


def clean_color_word(word):
    """
    Remove o '0' do Tam que vaza para o último token da cor.
    Ocorre quando x1 do token ultrapassa X_COLOR_MAX (226) e
    o PDF fundiu o último char da cor com o '0' do Tam.
    """
    if word["x1"] > X_COLOR_MAX and word["text"].endswith("0"):
        return word["text"][:-1]
    return word["text"]


def strip_unid_bleed(item_zone):
    """
    Remove o 'M' ou 'MT' da coluna Unid que vaza para o último token
    da descrição do item quando a descrição é longa (x1 > 148).
    """
    words = item_zone[2:]  # pula código e traço
    if not words:
        return ""
    texts = [w["text"] for w in words]
    last_word = words[-1]
    if last_word["x1"] > 148:
        t = texts[-1]
        if t.endswith("MT"):
            texts[-1] = t[:-2]
        elif t.endswith("M"):
            texts[-1] = t[:-1]
    return re.sub(r"^-\s*", "", " ".join(texts)).strip()


def group_rows(words, y_tol=2.0):
    rows = defaultdict(list)
    for w in words:
        key = round(w["top"] / y_tol) * y_tol
        rows[key].append(w)
    return dict(sorted(rows.items()))


def is_header_row(row_words):
    full = " ".join(w["text"] for w in row_words)
    return bool(SKIP_PATTERNS.search(full))


def extract_color(color_zone):
    """Extrai código e descrição de cor a partir da zona de cor."""
    if not color_zone:
        return None, None
    first  = color_zone[0]["text"]
    second = color_zone[1]["text"] if len(color_zone) > 1 else ""
    if COLOR_CODE_RE.match(first) and second == "-":
        code = first
        # Aplica limpeza do bleed em cada token da descrição
        desc = " ".join(clean_color_word(w) for w in color_zone[2:]).strip()
        return code, desc
    return None, None


def extract_abast(row_words):
    """Extrai o valor de Abast (negativo) da linha, se existir."""
    abast_zone = [w for w in row_words if X_ABAST_MIN <= w["x0"] < X_ABAST_MAX]
    for w in abast_zone:
        if ABAST_RE.match(w["text"]):
            return float(w["text"].replace(",", ""))
    return None


def extract_saldo_casa(row_words):
    """Extrai o saldo em casa da linha, se existir."""
    casa_zone = [w for w in row_words if X_SALDO_CASA_MIN <= w["x0"] < X_SALDO_CASA_MAX]
    for w in casa_zone:
        if NUMERIC_RE.match(w["text"]):
            # Mantém as casas decimais do PDF (ex.: 177.00000).
            return w["text"].replace(",", "")
    return None


def is_zero_saldo(value):
    if value is None or value == "":
        return True
    try:
        return float(str(value).replace(",", "")) == 0.0
    except ValueError:
        return False


def parse_pdf(pdf_path):
    """
    Retorna lista de itens originais (falta), cada um com:
      - item_code, item_desc
      - colors: [{color_code, color_desc, abast}]
      - substitutes: [{item_code, item_desc, colors: [{color_code, color_desc, abast}]}]
    """
    items        = []
    current_item = None
    current_sub  = None
    current_mini_fabrica = None

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # x_tolerance=1 impede que o 'M' da coluna Unid
            # seja fundido com o último char da descrição do item
            words = page.extract_words()  # default x_tolerance=3
            rows  = group_rows(words)

            for _y, row_words in rows.items():
                row_words = sorted(row_words, key=lambda w: w["x0"])
                row_text = " ".join(w["text"] for w in row_words).strip()

                # Captura contexto da mini fabrica vigente no relatorio.
                mini_match = MINI_FABRICA_RE.search(row_text)
                if mini_match:
                    current_mini_fabrica = f"Mini Fabrica - {mini_match.group(1).strip()}"
                    continue

                if is_header_row(row_words):
                    continue

                first_x = row_words[0]["x0"] if row_words else 999

                item_zone  = [w for w in row_words if w["x0"] < X_ITEM_DESC_MAX]
                color_orig = [w for w in row_words if X_COLOR_ORIG_MIN <= w["x0"] < X_COLOR_MAX]
                color_sub  = [w for w in row_words if X_COLOR_SUB_MIN  <= w["x0"] < X_COLOR_MAX]

                first_item_word = item_zone[0]["text"] if item_zone else ""
                is_new_code     = ITEM_CODE_RE.match(first_item_word)

                abast = extract_abast(row_words)
                saldo_casa = extract_saldo_casa(row_words)

                # ── ITEM ORIGINAL ─────────────────────────────────────────────
                if is_new_code and first_x < X_ORIGINAL_MAX:
                    item_desc = strip_unid_bleed(item_zone)
                    current_item = {
                        "item_code": first_item_word,
                        "item_desc": item_desc,
                        "mini_fabrica": current_mini_fabrica,
                        "colors":    [],
                        "_sub_saldo_by_color": {},
                    }
                    current_sub = False  # reset: não estamos em substituto
                    items.append(current_item)

                    code, desc = extract_color(color_orig)
                    if code:
                        current_item["colors"].append({
                            "color_code": code,
                            "color_desc": desc,
                            "abast":      abast,
                            "saldo_casa": saldo_casa,
                            "saldo_origem": "nacional",
                        })

                # ── SUBSTITUTO — ignorar, só marcar flag ──────────────────────
                elif is_new_code and X_ORIGINAL_MAX <= first_x < X_SUBSTITUTO_MAX:
                    current_sub = True  # linhas seguintes de cor pertencem ao sub → ignorar
                    # Guarda saldo do substituto por cor para usar como fallback
                    # quando o saldo do item nacional estiver zerado.
                    if current_item:
                        sub_code, _sub_desc = extract_color(color_sub)
                        sub_saldo_casa = extract_saldo_casa(row_words)
                        if sub_code and sub_saldo_casa is not None:
                            current_item["_sub_saldo_by_color"][sub_code] = sub_saldo_casa

                # ── CONTINUAÇÃO DE DESCRIÇÃO OU COR EXTRA ────────────────────
                else:
                    if item_zone and current_item and not is_new_code and first_x < X_ORIGINAL_MAX:
                        current_item["item_desc"] += " " + " ".join(w["text"] for w in item_zone)

                    # Cor extra de item original.
                    # Mesmo após linha de substituto, ainda podem existir linhas de cor
                    # do item original (ex.: começam com "M" e cor em x0~164).
                    if not is_new_code and color_orig and current_item:
                        code, desc = extract_color(color_orig)
                        if code:
                            current_item["colors"].append({
                                "color_code": code,
                                "color_desc": desc,
                                "abast":      abast,
                                "saldo_casa": saldo_casa,
                                "saldo_origem": "nacional",
                            })
                        elif current_item["colors"]:
                            extra = " ".join(clean_color_word(w) for w in color_orig).strip()
                            current_item["colors"][-1]["color_desc"] += " " + extra

    # Limpa espaços
    for it in items:
        sub_saldo_map = it.get("_sub_saldo_by_color", {})
        it["item_desc"] = it["item_desc"].strip()
        for c in it["colors"]:
            c["color_desc"] = c["color_desc"].strip()
            if is_zero_saldo(c.get("saldo_casa")) and c["color_code"] in sub_saldo_map:
                c["saldo_casa"] = sub_saldo_map[c["color_code"]]
                c["saldo_origem"] = "substituto"
            elif c.get("saldo_origem") is None:
                c["saldo_origem"] = "nacional"
        if "_sub_saldo_by_color" in it:
            del it["_sub_saldo_by_color"]

    return items


def save_json(items, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"[OK] JSON → {path}")


def save_html(items, html_path, source_label="Arquivo PDF"):
    template_path = os.getenv("PARSER_HTML_TEMPLATE", "templates/parser_report.html")
    if not os.path.isabs(template_path):
        template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), template_path)

    total_colors = sum(len(it.get("colors", [])) for it in items)
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    item_cards = []
    for it in items:
        item_code = escape(str(it.get("item_code", "")))
        item_desc = escape(str(it.get("item_desc", "")))
        mini_fabrica = escape(str(it.get("mini_fabrica", "Mini Fabrica - N/D")))
        header = f"[{mini_fabrica}] {item_code} - {item_desc}"

        color_rows = []
        for c in it.get("colors", []):
            color_code = escape(str(c.get("color_code", "")))
            color_desc = escape(str(c.get("color_desc", "")))
            abast = escape(str(c.get("abast", "")))
            saldo = escape(str(c.get("saldo_casa", "")))
            origem = escape(str(c.get("saldo_origem", "")))
            color_rows.append(
                f"<tr><td>{color_code}</td><td>{color_desc}</td><td>{abast}</td><td>{saldo}</td><td>{origem}</td></tr>"
            )

        empty_row = '<tr><td colspan="5">Sem cores</td></tr>'
        table_rows = "".join(color_rows) if color_rows else empty_row
        colors_table = (
            "<table><thead><tr>"
            "<th>Código da Cor</th><th>Descrição da Cor</th><th>Deve (Abast)</th><th>Saldo em Casa</th><th>Origem</th>"
            "</tr></thead>"
            f"<tbody>{table_rows}</tbody></table>"
        )
        item_cards.append(f"<section class=\"item-card\"><h3>{header}</h3>{colors_table}</section>")

    with open(template_path, "r", encoding="utf-8") as f:
        html_template = f.read()

    html = (
        html_template
        .replace("__REPORT_TITLE__", "Relatório Parseado")
        .replace("__SOURCE_FILE__", escape(source_label))
        .replace("__GENERATED_AT__", generated_at)
        .replace("__TOTAL_ITEMS__", str(len(items)))
        .replace("__TOTAL_COLORS__", str(total_colors))
        .replace("__ITEMS_HTML__", "".join(item_cards))
    )

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] HTML → {html_path}")


def save_common_html(common_items, html_path, source_label="Múltiplos PDFs"):
    items_for_report = []
    for it in common_items:
        mf_parts = []
        for mf in it.get("mini_fabricas", []):
            det = it.get("por_mini_fabrica", {}).get(mf, {})
            mf_parts.append(
                {
                    "color_code": it.get("color_code", ""),
                    "color_desc": f"{it.get('color_desc', '')} | {mf}",
                    "abast": det.get("abast", ""),
                    "saldo_casa": det.get("saldo_casa", ""),
                    "saldo_origem": det.get("saldo_origem", ""),
                }
            )
        items_for_report.append(
            {
                "item_code": it.get("item_code", ""),
                "item_desc": it.get("item_desc", ""),
                "mini_fabrica": "Itens em Comum",
                "colors": mf_parts,
            }
        )
    save_html(items_for_report, html_path, source_label=source_label)


def save_csv(items, path):
    columns = [
        "Mini Fabrica", "Codigo Item", "Descricao Item", "Codigo Cor",
        "Descricao Cor", "Deve (Abast)", "Saldo em Casa", "Origem do Saldo"
    ]
    records = []
    for it in items:
        for c in it["colors"]:
            records.append({
                "Mini Fabrica": it.get("mini_fabrica", ""),
                "Codigo Item": it["item_code"],
                "Descricao Item": it["item_desc"],
                "Codigo Cor": c["color_code"],
                "Descricao Cor": c["color_desc"],
                "Deve (Abast)": c.get("abast", ""),
                "Saldo em Casa": c.get("saldo_casa", ""),
                "Origem do Saldo": c.get("saldo_origem", ""),
            })

    # Mantém ordem de aparição do PDF (itens e cores), sem reordenação por código.
    df = pd.DataFrame.from_records(records, columns=columns)
    # CSV amigável para planilha e leitura direta.
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[OK] CSV  → {path}")


def print_summary(items):
    total_cores = sum(len(it["colors"]) for it in items)
    print(f"\n{'='*65}")
    print(f"  Itens: {len(items)}  |  Cores: {total_cores}")
    print(f"{'='*65}")
    for it in items:
        mf = it.get("mini_fabrica") or "Mini Fabrica - N/D"
        print(f"\n► [{mf}] {it['item_code']} - {it['item_desc']}")
        for c in it["colors"]:
            abast_str = f"  [deve: {c['abast']}]" if c.get("abast") is not None else ""
            casa_str = f"  [em casa: {c['saldo_casa']}]" if c.get("saldo_casa") is not None else ""
            origem_str = f"  [origem: {c.get('saldo_origem', 'nacional')}]"
            print(f"   └── {c['color_code']} - {c['color_desc']}{abast_str}{casa_str}{origem_str}")


def build_common_items(items):
    """
    Retorna combinações item+cor presentes em 2+ mini fábricas diferentes.
    """
    grouped = {}
    seq = 0
    for it in items:
        mf = it.get("mini_fabrica") or "Mini Fabrica - N/D"
        for c in it.get("colors", []):
            seq += 1
            key = (it.get("item_code"), c.get("color_code"))
            if key not in grouped:
                grouped[key] = {
                    "item_code": it.get("item_code"),
                    "item_desc": it.get("item_desc", ""),
                    "color_code": c.get("color_code"),
                    "color_desc": c.get("color_desc", ""),
                    "por_mini_fabrica": {},
                    "_first_seq": seq,
                    "_mini_fabricas_order": [],
                }
            if mf not in grouped[key]["por_mini_fabrica"]:
                grouped[key]["_mini_fabricas_order"].append(mf)
            grouped[key]["por_mini_fabrica"][mf] = {
                "abast": c.get("abast"),
                "saldo_casa": c.get("saldo_casa"),
                "saldo_origem": c.get("saldo_origem", "nacional"),
            }

    commons = []
    for entry in grouped.values():
        mini_fabs = entry["_mini_fabricas_order"]
        if len(mini_fabs) >= 2:
            entry["mini_fabricas"] = mini_fabs
            del entry["_mini_fabricas_order"]
            commons.append(entry)
        else:
            del entry["_mini_fabricas_order"]

    commons.sort(key=lambda x: x["_first_seq"])
    for entry in commons:
        del entry["_first_seq"]
    return commons


def save_common_json(common_items, path):
    payload = {"itens_comuns": common_items, "total_itens_comuns": len(common_items)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[OK] JSON Comuns → {path}")


def save_common_csv(common_items, path):
    columns = [
        "Codigo Item", "Descricao Item", "Codigo Cor", "Descricao Cor",
        "Mini Fabrica", "Deve (Abast)", "Saldo em Casa", "Origem do Saldo"
    ]
    records = []
    for it in common_items:
        for mf in it["mini_fabricas"]:
            det = it["por_mini_fabrica"].get(mf, {})
            records.append({
                "Codigo Item": it["item_code"],
                "Descricao Item": it["item_desc"],
                "Codigo Cor": it["color_code"],
                "Descricao Cor": it["color_desc"],
                "Mini Fabrica": mf,
                "Deve (Abast)": det.get("abast", ""),
                "Saldo em Casa": det.get("saldo_casa", ""),
                "Origem do Saldo": det.get("saldo_origem", ""),
            })

    # Mantém ordem natural dos itens comuns (primeira aparição no fluxo dos PDFs).
    df = pd.DataFrame.from_records(records, columns=columns)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[OK] CSV  Comuns → {path}")


def print_common_summary(common_items):
    print(f"\n{'='*65}")
    print(f"  Itens em Comum (mesmo item + mesma cor): {len(common_items)}")
    print(f"{'='*65}")
    for it in common_items:
        mfs = ", ".join(it["mini_fabricas"])
        print(f"\n► {it['item_code']} - {it['item_desc']} | {it['color_code']} - {it['color_desc']}")
        print(f"   └── Mini Fabricas: {mfs}")


if FASTAPI_AVAILABLE:
    app = FastAPI(
        title="PDF Parser API",
        version="2.0.0",
        summary="Parser de saldo de abastecimento (single e multi-PDF).",
        description=(
            "Processa um ou mais PDFs e gera saidas JSON/CSV consolidadas, "
            "incluindo mini fabrica, saldo em casa, origem do saldo e itens em comum."
        ),
    )

    def _output_dir() -> Path:
        output_dir = Path(os.getenv("PARSER_OUTPUT_DIR", "parsed_output"))
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _public_base_url(request: Request) -> str:
        forwarded_proto = request.headers.get("x-forwarded-proto")
        forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if forwarded_proto and forwarded_host:
            return f"{forwarded_proto}://{forwarded_host}".rstrip("/")
        return str(request.base_url).rstrip("/")

    def _safe_file_path(filename: str) -> Path:
        output_dir = _output_dir().resolve()
        file_path = (output_dir / filename).resolve()
        if output_dir not in file_path.parents and file_path != output_dir:
            raise HTTPException(status_code=400, detail="Arquivo invalido")
        if not file_path.is_file():
            raise HTTPException(status_code=404, detail="Arquivo nao encontrado")
        return file_path

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/files")
    def list_files():
        output_dir = _output_dir().resolve()
        files = []
        for path in sorted(output_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(output_dir).as_posix()
            files.append(
                {
                    "name": path.name,
                    "relative_path": rel,
                    "size_bytes": path.stat().st_size,
                    "updated_at": datetime.utcfromtimestamp(path.stat().st_mtime).isoformat() + "Z",
                }
            )
        return {"output_dir": str(output_dir), "count": len(files), "files": files}

    @app.get("/files/{filename:path}")
    def download_file(filename: str):
        file_path = _safe_file_path(filename)
        suffix = file_path.suffix.lower()
        media_type = "application/octet-stream"
        if suffix == ".json":
            media_type = "application/json"
        elif suffix == ".csv":
            media_type = "text/csv"
        elif suffix == ".html":
            media_type = "text/html"
        return FileResponse(str(file_path), filename=file_path.name, media_type=media_type)

    @app.post("/parse")
    async def parse(
        request: Request,
        files: Optional[List[UploadFile]] = File(default=None),
        file: Optional[UploadFile] = File(default=None),
    ):
        upload_files: List[UploadFile] = []
        if files:
            upload_files.extend(files)
        if file is not None:
            upload_files.append(file)

        if not upload_files:
            raise HTTPException(status_code=400, detail="Envie pelo menos um arquivo PDF.")
        if any(not (f.filename or "").lower().endswith(".pdf") for f in upload_files):
            raise HTTPException(status_code=400, detail="Todos os arquivos devem ser PDF.")

        temp_paths: List[Path] = []
        output_dir = _output_dir()
        base_url = _public_base_url(request)

        try:
            all_items = []
            uploaded_names = []
            for up in upload_files:
                uploaded_names.append(up.filename or "arquivo.pdf")
                suffix = Path(up.filename or "arquivo.pdf").suffix or ".pdf"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(await up.read())
                    temp_path = Path(tmp.name)
                temp_paths.append(temp_path)
                all_items.extend(parse_pdf(str(temp_path)))

            stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            if len(upload_files) == 1:
                base_name = Path(upload_files[0].filename or "arquivo").stem
                json_name = f"{base_name}_{stamp}_parsed.json"
                csv_name = f"{base_name}_{stamp}_parsed.csv"
                html_name = f"{base_name}_{stamp}_parsed.html"
                json_path = output_dir / json_name
                csv_path = output_dir / csv_name
                html_path = output_dir / html_name
                save_json(all_items, str(json_path))
                save_csv(all_items, str(csv_path))
                save_html(all_items, str(html_path), source_label=uploaded_names[0])
                common_items = []
                common_json_name = None
                common_csv_name = None
                common_html_name = None
            else:
                json_name = f"multi_pdf_{stamp}_parsed.json"
                csv_name = f"multi_pdf_{stamp}_parsed.csv"
                html_name = f"multi_pdf_{stamp}_parsed.html"
                common_json_name = f"multi_pdf_{stamp}_common_items.json"
                common_csv_name = f"multi_pdf_{stamp}_common_items.csv"
                common_html_name = f"multi_pdf_{stamp}_common_items.html"
                json_path = output_dir / json_name
                csv_path = output_dir / csv_name
                html_path = output_dir / html_name
                common_json_path = output_dir / common_json_name
                common_csv_path = output_dir / common_csv_name
                common_html_path = output_dir / common_html_name
                save_json(all_items, str(json_path))
                save_csv(all_items, str(csv_path))
                save_html(all_items, str(html_path), source_label=f"{len(uploaded_names)} PDFs")
                common_items = build_common_items(all_items)
                save_common_json(common_items, str(common_json_path))
                save_common_csv(common_items, str(common_csv_path))
                save_common_html(common_items, str(common_html_path), source_label=f"{len(uploaded_names)} PDFs")

            total_colors = sum(len(it["colors"]) for it in all_items)
            response = {
                "files": uploaded_names,
                "summary": {"total_items": len(all_items), "total_colors": total_colors},
                "outputs": {
                    "json": f"{base_url}/files/{quote(json_name)}",
                    "csv": f"{base_url}/files/{quote(csv_name)}",
                    "html": f"{base_url}/files/{quote(html_name)}",
                    "json_local_path": str(json_path),
                    "csv_local_path": str(csv_path),
                    "html_local_path": str(html_path),
                },
                "items": all_items,
            }
            if len(upload_files) > 1:
                response["summary"]["total_itens_comuns"] = len(common_items)
                response["outputs"]["common_json"] = f"{base_url}/files/{quote(common_json_name)}"
                response["outputs"]["common_csv"] = f"{base_url}/files/{quote(common_csv_name)}"
                response["outputs"]["common_html"] = f"{base_url}/files/{quote(common_html_name)}"

            return response
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Falha ao processar PDF(s): {e}")
        finally:
            for temp_path in temp_paths:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
else:
    app = None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python parse_falta.py <caminho_do_pdf1> [caminho_do_pdf2 ...]")
        sys.exit(1)

    pdf_paths = sys.argv[1:]

    # Modo único: preserva comportamento atual (1 PDF -> 1 par de saída).
    if len(pdf_paths) == 1:
        pdf_path = pdf_paths[0]
        print(f"Processando: {pdf_path}\n")
        items = parse_pdf(pdf_path)

        base = os.path.splitext(pdf_path)[0]
        save_json(items, base + "_parsed.json")
        save_csv(items,  base + "_parsed.csv")
        save_html(items, base + "_parsed.html", source_label=os.path.basename(pdf_path))
        print_summary(items)
    else:
        # Modo consolidado: 2+ PDFs -> somente 1 JSON + 1 CSV.
        all_items = []
        for idx, pdf_path in enumerate(pdf_paths, start=1):
            if idx > 1:
                print("\n" + "#" * 80 + "\n")
            print(f"Processando ({idx}/{len(pdf_paths)}): {pdf_path}")
            all_items.extend(parse_pdf(pdf_path))

        first_dir = os.path.dirname(os.path.abspath(pdf_paths[0]))
        out_json = os.path.join(first_dir, "multi_pdf_parsed.json")
        out_csv = os.path.join(first_dir, "multi_pdf_parsed.csv")
        out_html = os.path.join(first_dir, "multi_pdf_parsed.html")
        out_common_json = os.path.join(first_dir, "multi_pdf_common_items.json")
        out_common_csv = os.path.join(first_dir, "multi_pdf_common_items.csv")
        out_common_html = os.path.join(first_dir, "multi_pdf_common_items.html")

        print(f"\nGerando saída consolidada de {len(pdf_paths)} PDFs...\n")
        save_json(all_items, out_json)
        save_csv(all_items, out_csv)
        save_html(all_items, out_html, source_label=f"{len(pdf_paths)} PDFs")
        print_summary(all_items)

        common_items = build_common_items(all_items)
        save_common_json(common_items, out_common_json)
        save_common_csv(common_items, out_common_csv)
        save_common_html(common_items, out_common_html, source_label=f"{len(pdf_paths)} PDFs")
        print_common_summary(common_items)
