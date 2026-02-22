"""
Parser do Relatório de Saldo de Abastecimento (fab0257)
Extrai itens e suas respectivas cores a partir do PDF.

Uso:
    python parse_falta.py <caminho_do_pdf>

Saída:
    JSON com lista de itens e suas cores → <nome_arquivo>_parsed.json
    CSV com item_code, item_desc, color_code, color_desc → <nome_arquivo>_parsed.csv
"""

import sys, re, json, csv, os
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from collections import defaultdict
from html import escape

import pdfplumber
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

# ── Thresholds de coluna ─────────────────────────────────────────────────────
X_ITEM_MAX   = 145   # descrição do item fica em x0 < 145
X_COLOR_MIN  = 160   # coluna de cor começa ~164
X_COLOR_MAX  = 226   # dados numéricos começam ~230; ignorar a partir daqui

SKIP_PATTERNS = re.compile(
    r"(Empresa:|Filial:|Usuário:|Tipo\s+Relatório|Grupo\s+de|Grupo\s+POI|"
    r"fab0257|Relatório\s+de|Página|Descrição|Unid\.Cor|Mini\s+Fabrica|"
    r"Total\s+de|CALCADOS|BEIRA\s+RIO|S/A|FILIAL|Somente\s+Falta|"
    r"\d{2}/\d{2}/\d{4}|\d{2}:\d{2}:\d{2}|Usuário:)",
    re.IGNORECASE,
)

ITEM_CODE_RE  = re.compile(r"^\d{4,6}$")
COLOR_CODE_RE = re.compile(r"^\d{1,6}$")


def group_rows(words, y_tol=2.0):
    rows = defaultdict(list)
    for w in words:
        key = round(w["top"] / y_tol) * y_tol
        rows[key].append(w)
    return dict(sorted(rows.items()))


def is_header_row(row_words):
    full = " ".join(w["text"] for w in row_words)
    return bool(SKIP_PATTERNS.search(full))


def parse_pdf(pdf_path):
    items, current_item = [], None

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            rows  = group_rows(words)

            for _y, row_words in rows.items():
                if is_header_row(row_words):
                    continue

                item_zone  = [w for w in row_words if w["x0"] < X_ITEM_MAX]
                color_zone = [w for w in row_words if X_COLOR_MIN <= w["x0"] < X_COLOR_MAX]

                item_text  = " ".join(w["text"] for w in item_zone).strip()
                color_text = " ".join(w["text"] for w in color_zone).strip()

                first_item_word = item_zone[0]["text"] if item_zone else ""

                if ITEM_CODE_RE.match(first_item_word):
                    item_code = first_item_word
                    item_desc = " ".join(w["text"] for w in item_zone[1:])
                    item_desc = re.sub(r"^-\s*", "", item_desc).strip()
                    current_item = {"item_code": item_code, "item_desc": item_desc, "colors": []}
                    items.append(current_item)
                elif item_zone and current_item:
                    current_item["item_desc"] += " " + item_text

                if color_zone and current_item is not None:
                    first_word  = color_zone[0]["text"]
                    second_word = color_zone[1]["text"] if len(color_zone) > 1 else ""
                    if COLOR_CODE_RE.match(first_word) and second_word == "-":
                        color_code = first_word
                        color_desc = " ".join(w["text"] for w in color_zone[2:]).strip()
                        current_item["colors"].append({"color_code": color_code, "color_desc": color_desc})
                    else:
                        if current_item["colors"]:
                            current_item["colors"][-1]["color_desc"] += " " + color_text

    for it in items:
        it["item_desc"] = it["item_desc"].strip()
        for c in it["colors"]:
            c["color_desc"] = c["color_desc"].strip()

    return items


def save_json(items, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"[OK] JSON → {path}")


def save_csv(items, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["item_code", "item_desc", "color_code", "color_desc"])
        for it in items:
            for c in it["colors"]:
                writer.writerow([it["item_code"], it["item_desc"], c["color_code"], c["color_desc"]])
    print(f"[OK] CSV  → {path}")


def save_html(json_path, html_path, source_filename=""):
    template_path = os.getenv("PARSER_HTML_TEMPLATE", "templates/parser_report.html")
    if not os.path.isabs(template_path):
        template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), template_path)

    with open(json_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    total_colors = sum(len(it.get("colors", [])) for it in items)
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    item_cards = []
    for it in items:
        item_code = escape(str(it.get("item_code", "")))
        item_desc = escape(str(it.get("item_desc", "")))
        color_rows = []
        for c in it.get("colors", []):
            color_code = escape(str(c.get("color_code", "")))
            color_desc = escape(str(c.get("color_desc", "")))
            color_rows.append(
                f"<tr><td>{color_code}</td><td>{color_desc}</td></tr>"
            )

        empty_row = '<tr><td colspan="2">Sem cores</td></tr>'
        table_rows = "".join(color_rows) if color_rows else empty_row
        colors_table = (
            "<table><thead><tr><th>Código da Cor</th><th>Descrição da Cor</th></tr></thead>"
            f"<tbody>{table_rows}</tbody></table>"
        )
        item_cards.append(
            f"<section class=\"item-card\"><h3>{item_code} - {item_desc}</h3>{colors_table}</section>"
        )

    with open(template_path, "r", encoding="utf-8") as f:
        html_template = f.read()

    html = (
        html_template
        .replace("__REPORT_TITLE__", "Relatório Parseado")
        .replace("__SOURCE_FILE__", escape(source_filename or "Arquivo PDF"))
        .replace("__GENERATED_AT__", generated_at)
        .replace("__TOTAL_ITEMS__", str(len(items)))
        .replace("__TOTAL_COLORS__", str(total_colors))
        .replace("__ITEMS_HTML__", "".join(item_cards))
    )

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] HTML → {html_path}")


def print_summary(items):
    total_cores = sum(len(it["colors"]) for it in items)
    print(f"\n{'='*65}")
    print(f"  Itens: {len(items)}  |  Cores: {total_cores}")
    print(f"{'='*65}")
    for it in items:
        print(f"\n► {it['item_code']} - {it['item_desc']}")
        for c in it["colors"]:
            print(f"   └── {c['color_code']} - {c['color_desc']}")


app = FastAPI(title="PDF Parser API", version="1.0.0")


def _output_dir():
    output_dir = Path(os.getenv("PARSER_OUTPUT_DIR", "parsed_output"))
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _public_base_url(request: Request):
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if forwarded_proto and forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def _render_file_tree_html(output_dir: Path, base_url: str):
    entries = []
    for path in sorted(output_dir.rglob("*")):
        rel = path.relative_to(output_dir).as_posix()
        if rel.startswith("."):
            continue
        parts = rel.split("/")
        entries.append((parts, path.is_dir()))

    tree = {}
    for parts, is_dir in entries:
        node = tree
        for i, part in enumerate(parts):
            if part not in node:
                node[part] = {"__is_dir__": i < len(parts) - 1 or is_dir, "__children__": {}}
            node = node[part]["__children__"]

    def render_node(node, parent_path=""):
        rows = []
        for name in sorted(node.keys()):
            item = node[name]
            is_dir = item["__is_dir__"]
            current_path = f"{parent_path}/{name}" if parent_path else name
            safe_path = quote(current_path, safe="/")

            if is_dir:
                child_html = render_node(item["__children__"], current_path)
                rows.append(
                    f"<li><details open><summary>[DIR] {escape(name)}</summary>{child_html}</details></li>"
                )
            else:
                href = f"{base_url}/files/{safe_path}"
                rows.append(
                    f"<li>[FILE] <a href=\"{href}\" target=\"_blank\">{escape(name)}</a></li>"
                )
        return f"<ul>{''.join(rows)}</ul>"

    tree_html = render_node(tree) if tree else "<p>Nenhum arquivo gerado ainda.</p>"
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Arquivos do Parser</title>
  <style>
    body {{ font-family: "Segoe UI", Arial, sans-serif; margin: 24px; background: #f8fafc; color: #0f172a; }}
    .wrap {{ max-width: 980px; margin: 0 auto; background: #fff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 18px; }}
    h1 {{ margin: 0 0 8px; color: #0f766e; }}
    p {{ color: #475569; }}
    ul {{ list-style: none; padding-left: 20px; }}
    li {{ margin: 6px 0; }}
    a {{ color: #0f766e; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    summary {{ cursor: pointer; font-weight: 600; }}
    code {{ background: #f1f5f9; padding: 2px 6px; border-radius: 6px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Arquivos Gerados</h1>
    <p>Diretório: <code>{escape(str(output_dir))}</code></p>
    {tree_html}
  </div>
</body>
</html>"""


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/files")
@app.get("/files/")
def list_files(request: Request):
    output_dir = _output_dir().resolve()
    base_url = _public_base_url(request)
    html = _render_file_tree_html(output_dir, base_url)
    return HTMLResponse(content=html, media_type="text/html")


@app.get("/files/{filename:path}")
def download_file(filename: str):
    output_dir = _output_dir().resolve()
    file_path = (output_dir / filename).resolve()

    if output_dir not in file_path.parents and file_path != output_dir:
        raise HTTPException(status_code=400, detail="Arquivo invalido")
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Arquivo nao encontrado")

    suffix = file_path.suffix.lower()
    media_type = "application/octet-stream"
    if suffix == ".json":
        media_type = "application/json"
    elif suffix == ".csv":
        media_type = "text/csv"
    elif suffix == ".html":
        media_type = "text/html"

    if suffix == ".html":
        return FileResponse(
            str(file_path),
            media_type=media_type,
            headers={"Content-Disposition": f'inline; filename="{file_path.name}"'},
        )

    return FileResponse(str(file_path), filename=file_path.name, media_type=media_type)


@app.post("/parse")
async def parse(request: Request, file: UploadFile = File(...)):
    filename = file.filename or "arquivo.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Envie um arquivo PDF")

    output_dir = _output_dir()

    suffix = Path(filename).suffix or ".pdf"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        temp_path = tmp.name
        tmp.write(await file.read())

    try:
        print(f"Processando: {filename}\n")
        items = parse_pdf(temp_path)

        base_name = Path(filename).stem
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        file_base = f"{base_name}_{stamp}"
        json_name = f"{file_base}_parsed.json"
        csv_name = f"{file_base}_parsed.csv"
        html_name = f"{file_base}_parsed.html"

        json_path = output_dir / json_name
        csv_path = output_dir / csv_name
        html_path = output_dir / html_name

        save_json(items, str(json_path))
        save_csv(items, str(csv_path))
        save_html(str(json_path), str(html_path), source_filename=filename)
        print_summary(items)

        base_url = _public_base_url(request)
        json_url = f"{base_url}/files/{quote(json_name)}"
        csv_url = f"{base_url}/files/{quote(csv_name)}"
        html_url = f"{base_url}/files/{quote(html_name)}"

        total_colors = sum(len(it["colors"]) for it in items)
        return {
            "file": filename,
            "items": items,
            "summary": {"total_items": len(items), "total_colors": total_colors},
            "outputs": {
                "json": json_url,
                "csv": csv_url,
                "html": html_url,
                "json_local_path": str(json_path),
                "csv_local_path": str(csv_path),
                "html_local_path": str(html_path),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao processar PDF: {e}")
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python parse_falta.py <caminho_do_pdf>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    print(f"Processando: {pdf_path}\n")
    items = parse_pdf(pdf_path)

    base = os.path.splitext(pdf_path)[0]
    json_path = base + "_parsed.json"
    csv_path = base + "_parsed.csv"
    html_path = base + "_parsed.html"
    save_json(items, json_path)
    save_csv(items, csv_path)
    save_html(json_path, html_path, source_filename=os.path.basename(pdf_path))
    print_summary(items)
