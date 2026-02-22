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
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

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


TAGS_METADATA = [
    {
        "name": "status",
        "description": "Endpoints de verificacao e disponibilidade da API.",
    },
    {
        "name": "files",
        "description": "Navegacao e download dos arquivos gerados pelo parser.",
    },
    {
        "name": "parser",
        "description": "Upload de PDF e processamento para gerar saidas em JSON, CSV e HTML.",
    },
]


app = FastAPI(
    title="PDF Parser API",
    summary="API para processar PDF de saldo de abastecimento e gerar arquivos parseados.",
    description=(
        "API para upload de arquivos PDF e extracao estruturada de itens e cores.  \n"
        "As saidas sao disponibilizadas em JSON, CSV e HTML."
    ),
    version="1.0.0",
    docs_url=None,
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    openapi_tags=TAGS_METADATA,
    contact={
        "name": "Parser API Support",
        "url": "https://github.com/vinicius/n8n-manager",
    },
    license_info={
        "name": "MIT",
        "identifier": "MIT",
    },
)


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
    def _human_size(value):
        size = float(value)
        units = ["B", "KB", "MB", "GB", "TB"]
        for unit in units:
            if size < 1024.0 or unit == units[-1]:
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{int(value)} B"

    files = []
    folders = set()
    total_size = 0
    ext_counts = {}

    for path in sorted(output_dir.rglob("*")):
        rel = path.relative_to(output_dir).as_posix()
        if rel.startswith("."):
            continue

        if path.is_dir():
            folders.add(rel)
            continue

        stat = path.stat()
        name = path.name
        ext = (path.suffix.lower().lstrip(".") or "sem_ext")
        size = int(stat.st_size)
        mtime = int(stat.st_mtime)
        rel_dir = Path(rel).parent.as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        href = f"{base_url}/files/{quote(rel, safe='/')}"

        files.append({
            "name": name,
            "rel": rel,
            "dir": rel_dir,
            "size": size,
            "size_human": _human_size(size),
            "mtime": mtime,
            "mtime_iso": datetime.utcfromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "ext": ext,
            "href": href,
        })
        total_size += size
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

        current = Path(rel_dir)
        while str(current) and str(current) != ".":
            folders.add(current.as_posix())
            current = current.parent

    tree = {"dirs": {}, "files": []}
    for file_item in files:
        parts = file_item["rel"].split("/")
        node = tree
        for part in parts[:-1]:
            node = node["dirs"].setdefault(part, {"dirs": {}, "files": []})
        node["files"].append(file_item)

    def render_tree_node(node, parent=""):
        chunks = []
        for dirname in sorted(node["dirs"].keys()):
            child = node["dirs"][dirname]
            current_path = f"{parent}/{dirname}" if parent else dirname
            child_html = render_tree_node(child, current_path)
            chunks.append(
                f"<li class=\"dir\"><details open><summary><span class=\"badge\">DIR</span> {escape(dirname)}</summary>{child_html}</details></li>"
            )

        for file_item in sorted(node["files"], key=lambda item: item["name"].lower()):
            chunks.append(
                "<li class=\"file\">"
                f"<span class=\"badge badge-file\">FILE</span> "
                f"<a href=\"{file_item['href']}\" target=\"_blank\">{escape(file_item['name'])}</a>"
                f"<span class=\"meta\">{escape(file_item['size_human'])} . {escape(file_item['mtime_iso'])}</span>"
                "</li>"
            )
        return f"<ul class=\"tree-level\">{''.join(chunks)}</ul>"

    tree_html = render_tree_node(tree) if files else "<p class=\"empty\">Nenhum arquivo gerado ainda.</p>"
    ext_chips = "".join(
        f"<button class=\"chip\" data-ext=\"{escape(ext)}\">.{escape(ext)} ({count})</button>"
        for ext, count in sorted(ext_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    files_json = json.dumps(files, ensure_ascii=False)

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Arquivos do Parser</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=Syne:wght@500;700;800&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #0e1117;
      --panel: #151b23;
      --panel-soft: #1d2632;
      --line: #263244;
      --text: #e6edf3;
      --muted: #8ea0b8;
      --accent: #38bdf8;
      --accent-soft: #132636;
      --ok: #4ade80;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "IBM Plex Mono", monospace;
      color: var(--text);
      background:
        radial-gradient(circle at 10% 20%, #132035 0%, transparent 35%),
        radial-gradient(circle at 85% 0%, #1b2f23 0%, transparent 30%),
        var(--bg);
      min-height: 100vh;
      padding: 24px;
    }}
    .wrap {{
      max-width: 1240px;
      margin: 0 auto;
    }}
    .hero {{
      background: linear-gradient(120deg, #182231 0%, #161d27 55%, #102636 100%);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 24px;
      box-shadow: 0 18px 40px rgba(0, 0, 0, 0.35);
      margin-bottom: 16px;
    }}
    h1 {{
      margin: 0 0 6px;
      font-family: "Syne", sans-serif;
      font-size: clamp(28px, 5vw, 44px);
      letter-spacing: 0.02em;
      line-height: 1;
      color: #f0f9ff;
    }}
    .sub {{
      margin: 0;
      color: var(--muted);
      font-size: 13px;
    }}
    .stats {{
      margin-top: 14px;
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    }}
    .stat {{
      background: rgba(15, 23, 42, 0.6);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px 12px;
    }}
    .stat .k {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .08em; }}
    .stat .v {{ margin-top: 4px; font-size: 18px; font-weight: 600; color: #f8fafc; }}
    .toolbar {{
      display: grid;
      gap: 10px;
      grid-template-columns: 1fr 220px;
      margin: 14px 0 10px;
    }}
    .toolbar input, .toolbar select {{
      background: var(--panel);
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      font-family: inherit;
      font-size: 13px;
      width: 100%;
    }}
    .chips {{
      margin-top: 8px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .chip {{
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      padding: 6px 10px;
      border-radius: 999px;
      cursor: pointer;
      font-family: inherit;
      font-size: 12px;
    }}
    .chip.active {{
      border-color: var(--accent);
      background: var(--accent-soft);
      color: #d9f3ff;
    }}
    .layout {{
      display: grid;
      gap: 12px;
      grid-template-columns: 1.1fr .9fr;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      min-height: 420px;
    }}
    .panel h2 {{
      margin: 0 0 12px;
      font-family: "Syne", sans-serif;
      font-size: 20px;
      color: #dff4ff;
    }}
    .tree-level {{
      list-style: none;
      margin: 0;
      padding-left: 18px;
      border-left: 1px dashed #2b3a50;
    }}
    .tree-level > li {{
      margin: 8px 0;
    }}
    .dir > details > summary {{
      cursor: pointer;
      color: #e9f3ff;
      font-weight: 600;
    }}
    .badge {{
      display: inline-block;
      min-width: 42px;
      text-align: center;
      border-radius: 7px;
      padding: 2px 6px;
      font-size: 10px;
      border: 1px solid #355273;
      background: #162637;
      color: #9fd5ff;
      margin-right: 6px;
    }}
    .badge-file {{
      border-color: #36604c;
      background: #13261f;
      color: #b8ffd7;
    }}
    .file a {{
      color: #7dd3fc;
      text-decoration: none;
      word-break: break-all;
    }}
    .file a:hover {{
      text-decoration: underline;
    }}
    .meta {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      margin-top: 2px;
      margin-left: 50px;
    }}
    .result {{
      border: 1px solid #273244;
      border-radius: 10px;
      padding: 10px;
      margin-bottom: 8px;
      background: linear-gradient(180deg, rgba(17,24,39,.45), rgba(9,13,20,.5));
    }}
    .result .name {{
      color: #d6ecff;
      text-decoration: none;
      font-weight: 600;
      word-break: break-all;
    }}
    .result .name:hover {{ text-decoration: underline; }}
    .result .path {{ color: var(--muted); font-size: 11px; margin-top: 4px; }}
    .empty {{
      color: var(--muted);
      padding: 8px 0;
    }}
    @media (max-width: 980px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .toolbar {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>Parser Files Navigator</h1>
      <p class="sub">Raiz monitorada: <code>{escape(str(output_dir))}</code></p>
      <div class="stats">
        <div class="stat"><div class="k">Arquivos</div><div class="v">{len(files)}</div></div>
        <div class="stat"><div class="k">Pastas</div><div class="v">{len(folders)}</div></div>
        <div class="stat"><div class="k">Tamanho Total</div><div class="v">{_human_size(total_size)}</div></div>
      </div>
      <div class="toolbar">
        <input id="search" type="search" placeholder="Buscar por nome, caminho ou extensao...">
        <select id="sort">
          <option value="newest">Mais recentes</option>
          <option value="oldest">Mais antigos</option>
          <option value="name">Nome (A-Z)</option>
          <option value="size">Tamanho (maior primeiro)</option>
        </select>
      </div>
      <div class="chips">
        <button class="chip active" data-ext="all">Todos ({len(files)})</button>
        {ext_chips}
      </div>
    </section>
    <section class="layout">
      <article class="panel">
        <h2>Arvore de Pastas e Arquivos</h2>
        {tree_html}
      </article>
      <article class="panel">
        <h2>Resultados Filtrados</h2>
        <div id="results"></div>
      </article>
    </section>
  </div>
  <script>
    const FILES = {files_json};
    const resultsEl = document.getElementById("results");
    const searchEl = document.getElementById("search");
    const sortEl = document.getElementById("sort");
    const chips = Array.from(document.querySelectorAll(".chip"));
    let activeExt = "all";

    function sortFiles(arr, mode) {{
      const copy = arr.slice();
      if (mode === "newest") copy.sort((a, b) => b.mtime - a.mtime);
      else if (mode === "oldest") copy.sort((a, b) => a.mtime - b.mtime);
      else if (mode === "size") copy.sort((a, b) => b.size - a.size);
      else copy.sort((a, b) => a.name.localeCompare(b.name));
      return copy;
    }}

    function renderResults() {{
      const q = (searchEl.value || "").trim().toLowerCase();
      const sorted = sortFiles(FILES, sortEl.value);
      const filtered = sorted.filter((f) => {{
        const extOk = activeExt === "all" || f.ext === activeExt;
        if (!extOk) return false;
        if (!q) return true;
        return (
          f.name.toLowerCase().includes(q) ||
          f.rel.toLowerCase().includes(q) ||
          f.ext.toLowerCase().includes(q)
        );
      }});

      if (!filtered.length) {{
        resultsEl.innerHTML = '<p class="empty">Nenhum arquivo encontrado para o filtro atual.</p>';
        return;
      }}

      resultsEl.innerHTML = filtered.map((f) => `
        <article class="result">
          <a class="name" href="${{f.href}}" target="_blank">${{f.name}}</a>
          <div class="path">${{f.rel}}</div>
          <div class="path">${{f.size_human}} . ${{f.mtime_iso}} . .${{f.ext}}</div>
        </article>
      `).join("");
    }}

    chips.forEach((chip) => {{
      chip.addEventListener("click", () => {{
        chips.forEach((c) => c.classList.remove("active"));
        chip.classList.add("active");
        activeExt = chip.dataset.ext;
        renderResults();
      }});
    }});

    searchEl.addEventListener("input", renderResults);
    sortEl.addEventListener("change", renderResults);
    renderResults();
  </script>
</body>
</html>"""


@app.get(
    "/health",
    tags=["status"],
    summary="Health check",
    description="Retorna o estado de saude da API para monitoramento.",
)
def health():
    return {"status": "ok"}


@app.get("/docs", include_in_schema=False)
def docs_redirect():
    return RedirectResponse(url="/redoc")


@app.get(
    "/files",
    tags=["files"],
    summary="Lista arquivos gerados",
    description="Renderiza uma interface HTML para navegar e filtrar os arquivos parseados.",
)
@app.get("/files/", include_in_schema=False)
def list_files(request: Request):
    output_dir = _output_dir().resolve()
    base_url = _public_base_url(request)
    html = _render_file_tree_html(output_dir, base_url)
    return HTMLResponse(content=html, media_type="text/html")


@app.get(
    "/files/{filename:path}",
    tags=["files"],
    summary="Baixa ou visualiza arquivo gerado",
    description="Retorna um arquivo individual (JSON, CSV, HTML ou binario) da pasta de output.",
)
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


@app.post(
    "/parse",
    tags=["parser"],
    summary="Processa um PDF",
    description=(
        "Recebe um arquivo PDF, extrai itens e cores, e salva os resultados em JSON, CSV e HTML. "
        "Tambem retorna links publicos para os arquivos gerados."
    ),
)
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
