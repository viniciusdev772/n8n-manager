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
    from fastapi.openapi.utils import get_openapi
    from fastapi.responses import FileResponse
    from pydantic import BaseModel, Field
    FASTAPI_AVAILABLE = True
except ModuleNotFoundError:
    FastAPI = None
    File = None
    HTTPException = Exception
    Request = None
    UploadFile = None
    FileResponse = None
    get_openapi = None
    BaseModel = object
    Field = None
    FASTAPI_AVAILABLE = False

# ── Thresholds de coluna ─────────────────────────────────────────────────────
X_ORIGINAL_MAX   = 16.0   # item original começa em ~14.4  (x0 < 16)
X_SUBSTITUTO_MAX = 20.0   # substituto começa em ~17.6     (16 ≤ x0 < 20)
X_ITEM_DESC_MAX  = 145    # descrição do item termina antes de 145

X_COLOR_ORIG_MIN = 149    # inclui casos com código de cor "PAR..." em x0~149
X_COLOR_SUB_MIN  = 153    # cor do substituto começa ~156
X_COLOR_MAX      = 226    # dados numéricos começam ~230 (ignorar)

X_ABAST_MIN      = 240    # coluna Abast começa ~249
X_ABAST_MAX      = 290    # coluna Abast termina antes de ~290
X_TAM_MIN        = 226    # coluna Tam fica logo antes de Abast
X_TAM_MAX        = 240
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
COLOR_CODE_RE = re.compile(r"^(?:\d{1,6}|PAR\d{4,})$")
ABAST_RE      = re.compile(r"^-[\d,\.]+$")
NUMERIC_RE    = re.compile(r"^-?[\d,]+\.\d+$|^-?[\d,]+$")
TAM_RE        = re.compile(r"^\d{1,2}$")
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
        return None, None, ""

    # Procura o padrão "<codigo> - <descricao>" em qualquer posição da zona.
    # Isso evita falha quando há token de unidade ("M"/"MT") antes do código.
    for i in range(len(color_zone) - 1):
        first = color_zone[i]["text"]
        second = color_zone[i + 1]["text"]
        if COLOR_CODE_RE.match(first) and second == "-":
            par_tipo = "PAR" if first.startswith("PAR") else ""
            code = first[3:] if par_tipo else first
            desc = " ".join(clean_color_word(w) for w in color_zone[i + 2:]).strip()
            return code, desc, par_tipo
    return None, None, ""


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


def extract_tam(row_words):
    """Extrai o valor de Tam da linha, quando existir."""
    tam_zone = [w for w in row_words if X_TAM_MIN <= w["x0"] < X_TAM_MAX]
    for w in tam_zone:
        if TAM_RE.match(w["text"]):
            return w["text"]
    return ""


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
                tam = extract_tam(row_words)

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

                    code, desc, par_tipo = extract_color(color_orig)
                    if code:
                        current_item["colors"].append({
                            "color_code": code,
                            "color_desc": desc,
                            "par_tipo": par_tipo,
                            "tam": tam if par_tipo else "",
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
                        sub_code, _sub_desc, _sub_par_tipo = extract_color(color_sub)
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
                        code, desc, par_tipo = extract_color(color_orig)
                        if code:
                            current_item["colors"].append({
                                "color_code": code,
                                "color_desc": desc,
                                "par_tipo": par_tipo,
                                "tam": tam if par_tipo else "",
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
            if c.get("par_tipo") is None:
                c["par_tipo"] = ""
            if c.get("tam") is None:
                c["tam"] = ""
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
            par_tipo = escape(str(c.get("par_tipo", "")))
            color_code = escape(str(c.get("color_code", "")))
            color_desc = escape(str(c.get("color_desc", "")))
            tam = escape(str(c.get("tam", "")))
            abast = escape(str(c.get("abast", "")))
            saldo = escape(str(c.get("saldo_casa", "")))
            origem = escape(str(c.get("saldo_origem", "")))
            color_rows.append(
                f"<tr><td>{par_tipo}</td><td>{color_code}</td><td>{color_desc}</td><td>{tam}</td><td>{abast}</td><td>{saldo}</td><td>{origem}</td></tr>"
            )

        empty_row = '<tr><td colspan="7">Sem cores</td></tr>'
        table_rows = "".join(color_rows) if color_rows else empty_row
        colors_table = (
            "<table><thead><tr>"
            "<th>Tipo Unidade</th><th>Código da Cor</th><th>Descrição da Cor</th><th>Tam</th><th>Deve (Abast)</th><th>Saldo em Casa</th><th>Origem</th>"
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


def _build_common_distribution(common_items):
    distribution = []
    for it in common_items:
        destinos = []
        total_deve = 0.0
        for mf in it.get("mini_fabricas", []):
            det = it.get("por_mini_fabrica", {}).get(mf, {})
            abast = det.get("abast")
            need = abs(float(abast)) if abast is not None else 0.0
            total_deve += need
            destinos.append(
                {
                    "mini_fabrica": mf,
                    "deve_abast": abast,
                    "necessidade": need,
                    "saldo_casa": det.get("saldo_casa", ""),
                    "saldo_origem": det.get("saldo_origem", ""),
                }
            )
        distribution.append(
            {
                "item_code": it.get("item_code", ""),
                "item_desc": it.get("item_desc", ""),
                "tipo_unidade": it.get("par_tipo", ""),
                "codigo_cor": it.get("color_code", ""),
                "descricao_cor": it.get("color_desc", ""),
                "tam": it.get("tam", ""),
                "qtd_mini_fabricas": len(destinos),
                "total_necessidade": round(total_deve, 4),
                "mini_fabricas_destino": [d["mini_fabrica"] for d in destinos],
                "destinos": destinos,
            }
        )
    return distribution


def save_csv(items, path):
    columns = [
        "Mini Fabrica", "Codigo Item", "Descricao Item", "Tipo Unidade",
        "Codigo Cor", "Descricao Cor", "Tam",
        "Deve (Abast)", "Saldo em Casa", "Origem do Saldo"
    ]
    records = []
    for it in items:
        for c in it["colors"]:
            records.append({
                "Mini Fabrica": it.get("mini_fabrica", ""),
                "Codigo Item": it["item_code"],
                "Descricao Item": it["item_desc"],
                "Tipo Unidade": c.get("par_tipo", ""),
                "Codigo Cor": c["color_code"],
                "Descricao Cor": c["color_desc"],
                "Tam": c.get("tam", ""),
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
            par_str = f"  [tipo: {c.get('par_tipo')}]" if c.get("par_tipo") else ""
            tam_str = f"  [tam: {c.get('tam')}]" if c.get("tam") else ""
            abast_str = f"  [deve: {c['abast']}]" if c.get("abast") is not None else ""
            casa_str = f"  [em casa: {c['saldo_casa']}]" if c.get("saldo_casa") is not None else ""
            origem_str = f"  [origem: {c.get('saldo_origem', 'nacional')}]"
            print(f"   └── {c['color_code']} - {c['color_desc']}{par_str}{tam_str}{abast_str}{casa_str}{origem_str}")


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
            key = (it.get("item_code"), c.get("par_tipo", ""), c.get("color_code"), c.get("tam", ""))
            if key not in grouped:
                grouped[key] = {
                    "item_code": it.get("item_code"),
                    "item_desc": it.get("item_desc", ""),
                    "par_tipo": c.get("par_tipo", ""),
                    "color_code": c.get("color_code"),
                    "color_desc": c.get("color_desc", ""),
                    "tam": c.get("tam", ""),
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
    distribution = _build_common_distribution(common_items)
    payload = {
        "total_itens_comuns": len(common_items),
        "total_grupos_distribuicao": len(distribution),
        "grupos_distribuicao": distribution,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[OK] JSON Comuns → {path}")


def save_common_csv(common_items, path):
    distribution = _build_common_distribution(common_items)
    columns = [
        "Codigo Item", "Descricao Item", "Tipo Unidade", "Codigo Cor", "Descricao Cor", "Tam",
        "Qtd Mini Fabricas", "Mini Fabricas Destino", "Total Necessidade",
        "Necessidade por Mini (deve|saldo|origem)"
    ]
    records = []
    for it in distribution:
        detalhes = " || ".join(
            f"{d['mini_fabrica']}: deve={d.get('deve_abast','')} | saldo={d.get('saldo_casa','')} | origem={d.get('saldo_origem','')}"
            for d in it["destinos"]
        )
        records.append({
            "Codigo Item": it["item_code"],
            "Descricao Item": it["item_desc"],
            "Tipo Unidade": it.get("tipo_unidade", ""),
            "Codigo Cor": it["codigo_cor"],
            "Descricao Cor": it["descricao_cor"],
            "Tam": it.get("tam", ""),
            "Qtd Mini Fabricas": it["qtd_mini_fabricas"],
            "Mini Fabricas Destino": " | ".join(it["mini_fabricas_destino"]),
            "Total Necessidade": it["total_necessidade"],
            "Necessidade por Mini (deve|saldo|origem)": detalhes,
        })

    # Mantém ordem natural dos itens comuns (primeira aparição no fluxo dos PDFs).
    df = pd.DataFrame.from_records(records, columns=columns)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"[OK] CSV  Comuns → {path}")


def save_common_html(common_items, html_path, source_label="Múltiplos PDFs"):
    distribution = _build_common_distribution(common_items)
    data_json = json.dumps(distribution, ensure_ascii=False)
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    html = f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Painel de Itens em Comum</title>
  <style>
    :root {{
      --bg: #f4f6f8;
      --panel: #ffffff;
      --line: #dbe3ec;
      --text: #102236;
      --muted: #516277;
      --accent: #0054a6;
      --ok: #0f766e;
      --warn: #b45309;
    }}
    body {{ font-family: "Segoe UI", Tahoma, Arial, sans-serif; margin: 0; background: var(--bg); color: var(--text); }}
    .wrap {{ max-width: 1400px; margin: 0 auto; padding: 16px; }}
    .head {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 14px; margin-bottom: 12px; }}
    h2 {{ margin: 0; font-size: 22px; }}
    .sub {{ margin-top: 5px; color: var(--muted); font-size: 13px; }}
    .stats {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }}
    .pill {{ background: #edf4fb; border: 1px solid #c9ddef; color: #113c66; border-radius: 999px; padding: 6px 10px; font-size: 12px; font-weight: 600; }}
    .tools {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 12px; display: grid; grid-template-columns: 1.5fr 1fr 1fr 1fr auto; gap: 10px; margin-bottom: 12px; }}
    input, select {{ border: 1px solid #bfd0e2; border-radius: 10px; padding: 10px; font-size: 14px; background: #fff; }}
    label {{ font-size: 12px; color: var(--muted); font-weight: 600; margin-bottom: 4px; display: block; }}
    .toggle {{ display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--muted); }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; margin-bottom: 12px; overflow: hidden; }}
    .panel h3 {{ margin: 0; padding: 12px; border-bottom: 1px solid var(--line); font-size: 16px; }}
    .empty {{ padding: 16px; color: var(--muted); }}
    .note {{ font-size: 12px; color: var(--muted); padding: 0 12px 12px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #e7edf4; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f6f9fc; color: #294864; font-size: 12px; text-transform: uppercase; letter-spacing: .02em; }}
    .prio {{ font-weight: 700; color: var(--accent); }}
    .dest {{ font-weight: 700; }}
    .muted {{ color: var(--muted); }}
    .status-pill {{ display: inline-block; padding: 3px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; border: 1px solid transparent; }}
    .status-ok {{ background: #e6f7f2; color: #0f766e; border-color: #b7e7d9; }}
    .status-partial {{ background: #fff7e6; color: #b45309; border-color: #f4d7a4; }}
    .status-none {{ background: #fee2e2; color: #b91c1c; border-color: #fecaca; }}
    .group-card {{ border-top: 1px solid #e7edf4; }}
    .group-head {{ padding: 10px 12px; display: flex; justify-content: space-between; gap: 8px; cursor: pointer; background: #fcfdff; }}
    .group-title {{ font-weight: 700; font-size: 14px; }}
    .group-meta {{ color: var(--muted); font-size: 12px; margin-top: 2px; }}
    .badge {{ font-size: 12px; font-weight: 700; padding: 4px 8px; border-radius: 999px; border: 1px solid #d7e6f6; background: #eef6ff; color: #0b4a86; height: fit-content; }}
    .details {{ display: none; padding: 0 12px 12px; }}
    .open .details {{ display: block; }}
    @media (max-width: 980px) {{
      .tools {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="head">
      <h2>Painel de Distribuição de Itens em Comum</h2>
      <div class="sub">Fonte: {escape(source_label)} | Gerado em: {generated_at}</div>
      <div class="stats">
        <span class="pill">Grupos: {len(distribution)}</span>
        <span class="pill">Itens em comum: {len(common_items)}</span>
        <span class="pill" id="destinos-count">Destinos visíveis: 0</span>
        <span class="pill" id="coverage-overall">Cobertura geral: 0%</span>
      </div>
    </div>
    <div class="tools">
      <div>
        <label for="q">Busca rápida</label>
        <input id="q" placeholder="Item, descrição, cor ou mini fábrica...">
      </div>
      <div>
        <label for="mini">Filtrar mini fábrica</label>
        <select id="mini"><option value="">Todas as mini fábricas</option></select>
      </div>
      <div>
        <label for="sort">Ordenação</label>
        <select id="sort">
          <option value="prio">Prioridade operacional</option>
          <option value="need">Maior necessidade</option>
          <option value="item">Código do item</option>
          <option value="mini">Mais mini fábricas</option>
        </select>
      </div>
      <div>
        <label for="coverage">Filtro de Saldo Casa</label>
        <select id="coverage">
          <option value="">Todos</option>
          <option value="short">Com falta de saldo</option>
          <option value="ok">Saldo cobre total</option>
          <option value="zero">Sem saldo em casa</option>
        </select>
      </div>
      <label class="toggle"><input type="checkbox" id="onlyMulti"> Mostrar só itens que atendem 2+ minis</label>
    </div>

    <section class="panel">
      <h3>Roteiro de Envio (uma linha por destino)</h3>
      <table>
        <thead>
          <tr>
            <th>Prioridade</th>
            <th>Item</th>
            <th>Cor / Tam</th>
            <th>Mini Fábrica Destino</th>
            <th>Deve</th>
            <th>Saldo Casa</th>
            <th>Origem</th>
            <th>Necessidade</th>
            <th>Cobertura</th>
            <th>Saldo Após</th>
            <th>Abrange</th>
          </tr>
        </thead>
        <tbody id="dispatch-rows"></tbody>
      </table>
      <div class="empty" id="dispatch-empty" style="display:none">Nenhum destino encontrado com os filtros atuais.</div>
      <div class="note">Prioridade operacional considera necessidade do destino e abrangência do item para múltiplas mini fábricas.</div>
    </section>

    <section class="panel">
      <h3>Conferência por Item em Comum</h3>
      <div id="groups"></div>
      <div class="empty" id="groups-empty" style="display:none">Nenhum item comum encontrado com os filtros atuais.</div>
    </section>
  </div>
  <script>
    const DATA = {data_json};
    const dispatchRows = document.getElementById('dispatch-rows');
    const dispatchEmpty = document.getElementById('dispatch-empty');
    const grupos = document.getElementById('groups');
    const groupsEmpty = document.getElementById('groups-empty');
    const destinosCount = document.getElementById('destinos-count');
    const coverageOverall = document.getElementById('coverage-overall');
    const q = document.getElementById('q');
    const mini = document.getElementById('mini');
    const sort = document.getElementById('sort');
    const coverage = document.getElementById('coverage');
    const onlyMulti = document.getElementById('onlyMulti');

    function esc(v) {{
      return String(v ?? '').replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
    }}

    function parseNum(v) {{
      if (v === null || v === undefined || v === '') return 0;
      if (typeof v === 'number') return Number.isFinite(v) ? v : 0;
      const s = String(v).trim();
      const normalized = s.includes(',') && s.includes('.')
        ? s.replace(/\\./g, '').replace(',', '.')
        : s.replace(',', '.');
      const n = Number(normalized);
      return Number.isFinite(n) ? n : 0;
    }}

    function populateMiniFilter() {{
      const minis = Array.from(new Set(DATA.flatMap(g => g.mini_fabricas_destino || []))).sort((a,b)=>a.localeCompare(b));
      mini.innerHTML = '<option value="">Todas as mini fábricas</option>' + minis.map(m => `<option value="${{esc(m)}}">${{esc(m)}}</option>`).join('');
    }}

    function getStatus(covered, need) {{
      const eps = 1e-9;
      if (need <= eps) return 'ok';
      if (covered >= need - eps) return 'ok';
      if (covered > eps) return 'partial';
      return 'none';
    }}

    function enrichGroup(g) {{
      const destinosOrdenados = (g.destinos || []).slice().sort((a,b)=> parseNum(b.necessidade) - parseNum(a.necessidade));
      const saldoBase = destinosOrdenados.length
        ? Math.max(...destinosOrdenados.map(d => parseNum(d.saldo_casa)))
        : 0;
      let saldoRestante = saldoBase;
      const destinos = destinosOrdenados.map(d => {{
        const need = parseNum(d.necessidade);
        const covered = Math.min(Math.max(saldoRestante, 0), need);
        saldoRestante -= covered;
        return {{
          ...d,
          necessidade_num: need,
          saldo_base: saldoBase,
          covered,
          saldo_restante: Math.max(saldoRestante, 0),
          status: getStatus(covered, need),
          shortfall: Math.max(need - covered, 0),
          coverage_pct: need > 0 ? (covered / need) * 100 : 100
        }};
      }});
      const totalNeed = parseNum(g.total_necessidade);
      const coveredTotal = destinos.reduce((acc, d) => acc + d.covered, 0);
      const groupStatus = getStatus(coveredTotal, totalNeed);
      return {{
        ...g,
        saldo_base: saldoBase,
        total_need_num: totalNeed,
        covered_total: coveredTotal,
        uncovered_total: Math.max(totalNeed - coveredTotal, 0),
        coverage_pct: totalNeed > 0 ? (coveredTotal / totalNeed) * 100 : 100,
        group_status: groupStatus,
        destinos_enriched: destinos
      }};
    }}

    function statusBadge(status) {{
      if (status === 'ok') return '<span class="status-pill status-ok">Saldo cobre</span>';
      if (status === 'partial') return '<span class="status-pill status-partial">Cobertura parcial</span>';
      return '<span class="status-pill status-none">Sem cobertura</span>';
    }}

    function sortGroups(arr) {{
      const x = arr.slice();
      if (sort.value === 'mini') x.sort((a,b)=> (b.qtd_mini_fabricas||0) - (a.qtd_mini_fabricas||0));
      else if (sort.value === 'item') x.sort((a,b)=>(a.item_code||'').localeCompare(b.item_code||''));
      else if (sort.value === 'need') x.sort((a,b)=> parseNum(b.total_necessidade) - parseNum(a.total_necessidade));
      else {{
        x.sort((a,b)=> {{
          const pa = (parseNum(a.total_necessidade) * 10) + (a.qtd_mini_fabricas || 0);
          const pb = (parseNum(b.total_necessidade) * 10) + (b.qtd_mini_fabricas || 0);
          return pb - pa;
        }});
      }}
      return x;
    }}

    function filterGroups() {{
      const term = (q.value||'').toLowerCase();
      const miniSel = (mini.value || '').toLowerCase();
      const coverageSel = coverage.value || '';
      return sortGroups(DATA).map(enrichGroup).filter(g => {{
        const base = [
          g.item_code, g.item_desc, g.codigo_cor, g.descricao_cor, g.tipo_unidade, g.tam,
          ...(g.mini_fabricas_destino || []),
          ...(g.destinos || []).map(d => d.mini_fabrica)
        ].join(' ').toLowerCase();
        const okTerm = base.includes(term);
        const okMini = !miniSel || (g.mini_fabricas_destino || []).some(m => String(m).toLowerCase() === miniSel);
        const okMulti = !onlyMulti.checked || (g.qtd_mini_fabricas || 0) >= 2;
        const okCoverage = coverageSel === ''
          || (coverageSel === 'short' && g.uncovered_total > 0)
          || (coverageSel === 'ok' && g.uncovered_total <= 1e-9)
          || (coverageSel === 'zero' && g.saldo_base <= 1e-9);
        return okTerm && okMini && okMulti && okCoverage;
      }});
    }}

    function renderDispatch(groups) {{
      const rows = [];
      let sumNeed = 0;
      let sumCovered = 0;
      groups.forEach(g => {{
        sumNeed += g.total_need_num || 0;
        sumCovered += g.covered_total || 0;
        const groupPrio = (parseNum(g.total_necessidade) * 10) + (g.qtd_mini_fabricas || 0);
        (g.destinos_enriched || []).forEach(d => {{
          const rowPrio = (d.shortfall * 100) + (d.necessidade_num * 10) + (g.qtd_mini_fabricas || 0);
          rows.push({{
            ...d,
            item_code: g.item_code,
            item_desc: g.item_desc,
            tipo_unidade: g.tipo_unidade || '',
            codigo_cor: g.codigo_cor,
            descricao_cor: g.descricao_cor,
            tam: g.tam || '',
            qtd_mini_fabricas: g.qtd_mini_fabricas || 0,
            total_necessidade: g.total_necessidade || 0,
            group_prio: groupPrio + d.shortfall * 100,
            row_prio: rowPrio
          }});
        }});
      }});
      rows.sort((a,b)=> b.row_prio - a.row_prio);
      destinosCount.textContent = `Destinos visíveis: ${{rows.length}}`;
      const pct = sumNeed > 0 ? (sumCovered / sumNeed) * 100 : 100;
      coverageOverall.textContent = `Cobertura geral: ${{pct.toFixed(1)}}%`;
      if (!rows.length) {{
        dispatchRows.innerHTML = '';
        dispatchEmpty.style.display = 'block';
        return;
      }}
      dispatchEmpty.style.display = 'none';
      dispatchRows.innerHTML = rows.map(r => `
        <tr>
          <td class="prio">${{r.row_prio.toFixed(1)}}</td>
          <td>
            <div><strong>${{esc(r.item_code)}} - ${{esc(r.item_desc)}}</strong></div>
            <div class="muted">Tipo: ${{esc(r.tipo_unidade || '-')}}</div>
          </td>
          <td>
            <div><strong>${{esc(r.codigo_cor)}} - ${{esc(r.descricao_cor)}}</strong></div>
            <div class="muted">Tam: ${{esc(r.tam || '-')}}</div>
          </td>
          <td class="dest">${{esc(r.mini_fabrica || '')}}</td>
          <td>${{esc(r.deve_abast ?? '')}}</td>
          <td>${{esc(r.saldo_casa ?? '')}}</td>
          <td>${{esc(r.saldo_origem ?? '')}}</td>
          <td><strong>${{esc(r.necessidade ?? '')}}</strong></td>
          <td>${{statusBadge(r.status)}} ${{esc(r.coverage_pct.toFixed(0))}}%</td>
          <td>${{esc(r.saldo_restante.toFixed(3))}}</td>
          <td>${{esc(String(r.qtd_mini_fabricas))}} minis</td>
        </tr>
      `).join('');
    }}

    function renderGroups(groups) {{
      if (!groups.length) {{
        grupos.innerHTML = '';
        groupsEmpty.style.display = 'block';
        return;
      }}
      groupsEmpty.style.display = 'none';
      grupos.innerHTML = groups.map((g, idx) => `
        <article class="group-card" data-idx="${{idx}}">
          <div class="group-head">
            <div>
              <div class="group-title">${{esc(g.item_code)}} - ${{esc(g.item_desc)}}</div>
              <div class="group-meta">
                Cor: ${{esc(g.codigo_cor)}} - ${{esc(g.descricao_cor)}} | Tam: ${{esc(g.tam || '-')}} |
                Atende ${{esc(String(g.qtd_mini_fabricas))}} mini(s) | Necessidade total: ${{esc(String(g.total_necessidade))}} |
                Saldo Casa: ${{esc(g.saldo_base.toFixed(3))}} | Falta: ${{esc(g.uncovered_total.toFixed(3))}}
              </div>
            </div>
            <span class="badge">Prioridade ${{((parseNum(g.total_necessidade) * 10) + (g.qtd_mini_fabricas || 0) + (g.uncovered_total * 100)).toFixed(1)}} • ${{g.coverage_pct.toFixed(0)}}%</span>
          </div>
          <div class="details">
            <table>
              <thead><tr><th>Mini Fábrica</th><th>Deve (Abast)</th><th>Saldo em Casa</th><th>Origem do Saldo</th><th>Necessidade</th><th>Cobertura</th><th>Saldo Após</th></tr></thead>
              <tbody>
                ${{(g.destinos_enriched || []).map(d=>`
                  <tr>
                    <td><strong>${{esc(d.mini_fabrica ?? '')}}</strong></td>
                    <td>${{esc(d.deve_abast ?? '')}}</td>
                    <td>${{esc(d.saldo_casa ?? '')}}</td>
                    <td>${{esc(d.saldo_origem ?? '')}}</td>
                    <td><strong>${{esc(d.necessidade ?? '')}}</strong></td>
                    <td>${{statusBadge(d.status)}} ${{esc(d.coverage_pct.toFixed(0))}}%</td>
                    <td>${{esc(d.saldo_restante.toFixed(3))}}</td>
                  </tr>
                `).join('')}}
              </tbody>
            </table>
          </div>
        </article>
      `).join('');
      Array.from(document.querySelectorAll('.group-head')).forEach(head => {{
        head.onclick = () => head.closest('.group-card').classList.toggle('open');
      }});
    }}

    function render() {{
      const groups = filterGroups();
      renderDispatch(groups);
      renderGroups(groups);
    }}

    populateMiniFilter();
    q.addEventListener('input', render);
    mini.addEventListener('change', render);
    sort.addEventListener('change', render);
    coverage.addEventListener('change', render);
    onlyMulti.addEventListener('change', render);
    render();
  </script>
</body>
</html>"""
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] HTML Comuns → {html_path}")


def print_common_summary(common_items):
    distribution = _build_common_distribution(common_items)
    print(f"\n{'='*65}")
    print(f"  Itens em Comum (mesmo item + mesma cor): {len(common_items)}")
    print(f"{'='*65}")
    for it in distribution:
        mfs = ", ".join(it["mini_fabricas_destino"])
        print(f"\n► {it['item_code']} - {it['item_desc']} | {it['codigo_cor']} - {it['descricao_cor']}")
        print(f"   └── Destinos ({it['qtd_mini_fabricas']}): {mfs} | necessidade total: {it['total_necessidade']}")


if FASTAPI_AVAILABLE:
    TAGS_METADATA = [
        {
            "name": "status",
            "description": "Saude e disponibilidade da API.",
        },
        {
            "name": "files",
            "description": "Navegacao e download dos arquivos gerados em JSON/CSV/HTML.",
        },
        {
            "name": "parser",
            "description": (
                "Processamento de PDF(s) com extracao de itens/cores, mini fabrica, "
                "saldo em casa, origem do saldo, tipo PAR e Tam, alem da distribuicao de itens comuns."
            ),
        },
    ]

    class HealthResponse(BaseModel):
        status: str = Field(description="Status da API.", examples=["ok"])

    class GeneratedFile(BaseModel):
        name: str = Field(description="Nome do arquivo.")
        relative_path: str = Field(description="Caminho relativo dentro da pasta de output.")
        size_bytes: int = Field(description="Tamanho em bytes.")
        updated_at: str = Field(description="Timestamp UTC ISO-8601 de atualizacao.")
        direct_url: str = Field(
            description="Link direto para download/abertura do arquivo via endpoint /files/{filename}."
        )

    class FileListResponse(BaseModel):
        output_dir: str
        count: int
        files: List[GeneratedFile]

    class ParseColor(BaseModel):
        color_code: str
        color_desc: str
        par_tipo: str = Field(default="", description="Tipo de unidade, ex.: PAR.")
        tam: str = Field(default="", description="Tamanho (Tam), quando aplicavel.")
        abast: Optional[float] = None
        saldo_casa: Optional[str] = None
        saldo_origem: str = Field(default="nacional")

    class ParseItem(BaseModel):
        item_code: str
        item_desc: str
        mini_fabrica: Optional[str] = None
        colors: List[ParseColor]

    class ParseSummary(BaseModel):
        total_items: int
        total_colors: int
        total_itens_comuns: int = 0

    class ParseOutputs(BaseModel):
        json: str = Field(description="Link direto do JSON principal.")
        csv: str = Field(description="Link direto do CSV principal.")
        html: str = Field(description="Link direto do HTML principal.")
        json_url: str = Field(description="Alias explícito para o link direto do JSON principal.")
        csv_url: str = Field(description="Alias explícito para o link direto do CSV principal.")
        html_url: str = Field(description="Alias explícito para o link direto do HTML principal.")
        json_local_path: str = Field(description="Caminho local do JSON principal no servidor.")
        csv_local_path: str = Field(description="Caminho local do CSV principal no servidor.")
        html_local_path: str = Field(description="Caminho local do HTML principal no servidor.")
        common_json: Optional[str] = Field(default=None, description="Link direto do JSON de itens em comum.")
        common_csv: Optional[str] = Field(default=None, description="Link direto do CSV de itens em comum.")
        common_html: Optional[str] = Field(default=None, description="Link direto do HTML de itens em comum.")
        common_json_url: Optional[str] = Field(
            default=None, description="Alias explícito para o link direto do JSON de itens em comum."
        )
        common_csv_url: Optional[str] = Field(
            default=None, description="Alias explícito para o link direto do CSV de itens em comum."
        )
        common_html_url: Optional[str] = Field(
            default=None, description="Alias explícito para o link direto do HTML de itens em comum."
        )

    class ParseResponse(BaseModel):
        files: List[str]
        summary: ParseSummary
        outputs: ParseOutputs
        items: List[ParseItem]

    app = FastAPI(
        title="PDF Parser API",
        version="2.0.0",
        summary="Parser de saldo de abastecimento (single e multi-PDF).",
        description=(
            "API para processar relatórios PDF (fab0257) e gerar saídas estruturadas.\n\n"
            "Funcionalidades atuais:\n"
            "- Upload de 1 ou vários PDFs no mesmo request.\n"
            "- Extração de item, cor, mini fábrica, abast (deve), saldo em casa e origem do saldo.\n"
            "- Suporte a linhas de cor no formato numérico e no formato PAR (ex.: PAR107848).\n"
            "- Extração de Tam (tamanho) para linhas PAR.\n"
            "- Fallback de saldo do substituto quando saldo do nacional é zero.\n"
            "- Geração de JSON, CSV e HTML para parse principal.\n"
            "- Geração de JSON, CSV e HTML de itens comuns (distribuição entre mini fábricas).\n"
            "- Listagem e download dos arquivos gerados via API."
        ),
        openapi_tags=TAGS_METADATA,
        contact={"name": "Parser API Support", "url": "https://github.com/vinicius/n8n-manager"},
        license_info={"name": "MIT", "identifier": "MIT"},
    )

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
            contact=app.contact,
            license_info=app.license_info,
        )
        openapi_schema["externalDocs"] = {
            "description": "Repositório do projeto",
            "url": "https://github.com/vinicius/n8n-manager",
        }
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi

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

    @app.get(
        "/health",
        tags=["status"],
        summary="Health check da API",
        description="Retorna estado de disponibilidade da API.",
        response_model=HealthResponse,
    )
    def health():
        return {"status": "ok"}

    @app.get(
        "/files",
        tags=["files"],
        summary="Lista arquivos gerados",
        description=(
            "Lista todos os arquivos presentes na pasta de output da parser API. "
            "Inclui nome, caminho relativo, tamanho e data de atualização."
        ),
        response_model=FileListResponse,
    )
    def list_files(request: Request):
        output_dir = _output_dir().resolve()
        base_url = _public_base_url(request)
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
                    "direct_url": f"{base_url}/files/{quote(rel)}",
                }
            )
        return {"output_dir": str(output_dir), "count": len(files), "files": files}

    @app.get(
        "/files/{filename:path}",
        tags=["files"],
        summary="Download de arquivo gerado",
        description=(
            "Faz download de um arquivo gerado pela API (JSON, CSV, HTML ou binário). "
            "O caminho deve ser relativo à pasta de output."
        ),
        responses={
            200: {"description": "Arquivo retornado com sucesso."},
            400: {"description": "Arquivo inválido."},
            404: {"description": "Arquivo não encontrado."},
        },
    )
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

    @app.post(
        "/parse",
        tags=["parser"],
        summary="Processa 1 ou vários PDFs",
        description=(
            "Recebe arquivo(s) PDF e gera saídas estruturadas.\n\n"
            "Entradas aceitas:\n"
            "- `file`: envio de 1 PDF\n"
            "- `files`: envio de múltiplos PDFs no mesmo request\n\n"
            "Saídas geradas:\n"
            "- Parse principal: JSON, CSV e HTML\n"
            "- Itens comuns (distribuição): JSON, CSV e HTML\n\n"
            "Links diretos:\n"
            "- O campo `outputs` retorna links diretos de todos os arquivos gerados (`json/csv/html` e `common_*`).\n"
            "- O endpoint `/files` lista cada arquivo com `direct_url` pronto para download/abertura.\n\n"
            "Regras aplicadas no parse:\n"
            "- Mini fábrica por contexto de cabeçalho do relatório\n"
            "- Cor numérica e cor PAR (com `Tipo Unidade=PAR` e `Tam`)\n"
            "- `saldo_casa` com fallback para substituto quando saldo nacional é zero"
        ),
        response_model=ParseResponse,
        responses={
            200: {"description": "PDF(s) processado(s) com sucesso."},
            400: {"description": "Arquivo ausente ou extensão inválida."},
            500: {"description": "Falha durante o processamento."},
        },
    )
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
                common_json_name = f"{base_name}_{stamp}_common_items.json"
                common_csv_name = f"{base_name}_{stamp}_common_items.csv"
                common_html_name = f"{base_name}_{stamp}_common_items.html"
                json_path = output_dir / json_name
                csv_path = output_dir / csv_name
                html_path = output_dir / html_name
                common_json_path = output_dir / common_json_name
                common_csv_path = output_dir / common_csv_name
                common_html_path = output_dir / common_html_name
                save_json(all_items, str(json_path))
                save_csv(all_items, str(csv_path))
                save_html(all_items, str(html_path), source_label=uploaded_names[0])
                common_items = build_common_items(all_items)
                save_common_json(common_items, str(common_json_path))
                save_common_csv(common_items, str(common_csv_path))
                save_common_html(common_items, str(common_html_path), source_label=uploaded_names[0])
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
                    "json_url": f"{base_url}/files/{quote(json_name)}",
                    "csv_url": f"{base_url}/files/{quote(csv_name)}",
                    "html_url": f"{base_url}/files/{quote(html_name)}",
                    "json_local_path": str(json_path),
                    "csv_local_path": str(csv_path),
                    "html_local_path": str(html_path),
                },
                "items": all_items,
            }
            response["summary"]["total_itens_comuns"] = len(common_items)
            response["outputs"]["common_json"] = f"{base_url}/files/{quote(common_json_name)}"
            response["outputs"]["common_csv"] = f"{base_url}/files/{quote(common_csv_name)}"
            response["outputs"]["common_html"] = f"{base_url}/files/{quote(common_html_name)}"
            response["outputs"]["common_json_url"] = f"{base_url}/files/{quote(common_json_name)}"
            response["outputs"]["common_csv_url"] = f"{base_url}/files/{quote(common_csv_name)}"
            response["outputs"]["common_html_url"] = f"{base_url}/files/{quote(common_html_name)}"

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
        common_items = build_common_items(items)
        save_common_json(common_items, base + "_common_items.json")
        save_common_csv(common_items, base + "_common_items.csv")
        save_common_html(common_items, base + "_common_items.html", source_label=os.path.basename(pdf_path))
        print_common_summary(common_items)
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
