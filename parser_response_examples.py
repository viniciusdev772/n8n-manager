"""Response examples used by parser API docs (Swagger/ReDoc)."""

HEALTH_RESPONSES = {
    200: {
        "description": "API disponivel.",
        "content": {
            "application/json": {
                "examples": {
                    "ok": {
                        "summary": "Servico saudavel",
                        "value": {"status": "ok"},
                    }
                }
            }
        },
    }
}

PARSE_RESPONSES = {
    200: {
        "description": "PDF processado com sucesso.",
        "content": {
            "application/json": {
                "examples": {
                    "parse_completo_real": {
                        "summary": "Resposta completa real de producao",
                        "value": {
                            "file": "1743709282597_falta.pdf",
                            "items": [
                                {
                                    "item_code": "44187",
                                    "item_desc": "CACHAREL SOFT(B P/V 11/12)+ESP PU 1,5 D30+SPONL 40GR ANTI-MICROBIANO",
                                    "colors": [{"color_code": "35313", "color_desc": "CREME 516"}],
                                },
                                {
                                    "item_code": "59124",
                                    "item_desc": "SARJA 7030 RESINADA",
                                    "colors": [{"color_code": "15745", "color_desc": "PRETO 01"}],
                                },
                                {
                                    "item_code": "67345",
                                    "item_desc": "NAPA TURIM PU 1.0 + SARJA 7030/40 0.40",
                                    "colors": [
                                        {"color_code": "86348", "color_desc": "VERMELHO 1025/CRU 20"},
                                        {"color_code": "103963", "color_desc": "CONHAQUE 1273/PRETO 01"},
                                    ],
                                },
                                {
                                    "item_code": "81685",
                                    "item_desc": "VELUDO VIENA (M O/I 2017)+SARJA 7030/40",
                                    "colors": [{"color_code": "15860", "color_desc": "PRETO/PRETO0 01"}],
                                },
                                {
                                    "item_code": "84549",
                                    "item_desc": "VERNIZ PREMIUM PU 1.0 (M/MD O/I 2017)",
                                    "colors": [{"color_code": "86347", "color_desc": "VERMELHO 1025"}],
                                },
                                {
                                    "item_code": "89072",
                                    "item_desc": "METALIZADO PREMIUM PU 0.9 (M/MD P/V 17/18)",
                                    "colors": [{"color_code": "74941", "color_desc": "GRAFITE 914"}],
                                },
                                {
                                    "item_code": "108850",
                                    "item_desc": "NAPA PELE STRECH PU 1.0 (MD O/I 2019)",
                                    "colors": [
                                        {"color_code": "35312", "color_desc": "BRANCO OFF 526"},
                                        {"color_code": "83508", "color_desc": "CAFE 987"},
                                        {"color_code": "101433", "color_desc": "CONHAQUE 1273"},
                                    ],
                                },
                                {
                                    "item_code": "112008",
                                    "item_desc": "NAPA PELE STRECH PU 1.0 (MD O/I 2019)+SARJA 7030/40",
                                    "colors": [
                                        {"color_code": "35324", "color_desc": "BRANCO OFF 526/CRU 20"},
                                        {"color_code": "95810", "color_desc": "CAMEL 1165/CRU 20"},
                                    ],
                                },
                                {
                                    "item_code": "140206",
                                    "item_desc": "NAPA FLOATHER ZURIQUE PU 1.2- IMPORTADO",
                                    "colors": [
                                        {"color_code": "15745", "color_desc": "PRETO 01"},
                                        {"color_code": "35312", "color_desc": "BRANCO OFF 526"},
                                        {"color_code": "95669", "color_desc": "CARAMELO 1170"},
                                    ],
                                },
                                {
                                    "item_code": "140345",
                                    "item_desc": "NAPA BERLIM PU 1.2- IMPORTADO",
                                    "colors": [{"color_code": "35312", "color_desc": "BRANCO OFF 526"}],
                                },
                                {
                                    "item_code": "157666",
                                    "item_desc": "TECIDO ADTERM CH 180 C CONFORFLEX T7 EXT 400 C",
                                    "colors": [{"color_code": "15745", "color_desc": "PRETO 01"}],
                                },
                                {
                                    "item_code": "172897",
                                    "item_desc": "NAPA GENEBRA PU 1.2+TECIDO LECCE",
                                    "colors": [{"color_code": "73437", "color_desc": "CAFE 856/PRETO 01"}],
                                },
                                {
                                    "item_code": "172898",
                                    "item_desc": "NAPA FLOATHER ZURIQUE PU 1.2+TECIDO LECCE",
                                    "colors": [
                                        {"color_code": "84081", "color_desc": "CAFE 987/PRETO 01"},
                                        {"color_code": "95740", "color_desc": "CARAMELO 1170/CREME 516"},
                                    ],
                                },
                                {
                                    "item_code": "175505",
                                    "item_desc": "NOBUCK IGUANA PU 0.9 (B P/V 24/25)+SARJA 7030/50",
                                    "colors": [{"color_code": "84042", "color_desc": "CREME 985/CRU 20"}],
                                },
                                {
                                    "item_code": "25567",
                                    "item_desc": "AVESSO VELUR",
                                    "colors": [{"color_code": "3", "color_desc": "PRETO"}],
                                },
                                {
                                    "item_code": "32468",
                                    "item_desc": "NOBUCK NEW PU 0.8 NOSSA BASE(P/V 09/10)",
                                    "colors": [{"color_code": "15745", "color_desc": "PRETO 01"}],
                                },
                                {
                                    "item_code": "56577",
                                    "item_desc": "FORRO MILANO PU 0.7(B O/I 2014)",
                                    "colors": [{"color_code": "15745", "color_desc": "PRETO 01"}],
                                },
                                {
                                    "item_code": "63609",
                                    "item_desc": "FORRO MILANO PU 0.7+PX25+FILME PU 400",
                                    "colors": [{"color_code": "50", "color_desc": "NATURAL"}],
                                },
                                {
                                    "item_code": "67244",
                                    "item_desc": "NAPA TURIM PU 1.0 (B P/V 15/16)",
                                    "colors": [
                                        {"color_code": "15745", "color_desc": "PRETO 01"},
                                        {"color_code": "52531", "color_desc": "NUDE 658"},
                                        {"color_code": "83517", "color_desc": "CREME 985"},
                                        {"color_code": "95373", "color_desc": "CAMEL 1165"},
                                        {"color_code": "101433", "color_desc": "CONHAQUE 1273"},
                                        {"color_code": "103102", "color_desc": "CHERRY 1335"},
                                    ],
                                },
                                {
                                    "item_code": "70815",
                                    "item_desc": "CAMURCAO PU 1.2 (M O/I 2016)+BASE FLEX 1.0",
                                    "colors": [
                                        {"color_code": "66258", "color_desc": "CARAMELO 787/BISCOITO"},
                                        {"color_code": "90329", "color_desc": "TAN 1080/BISCOITO"},
                                        {"color_code": "96720", "color_desc": "BRANCO OFF 1164/BISCOITO"},
                                    ],
                                },
                                {
                                    "item_code": "73034",
                                    "item_desc": "NAPA TURIM PU 1.0 (B P/V 15/16)+BASE FLEX 1.0",
                                    "colors": [
                                        {"color_code": "37331", "color_desc": "BRANCO OFF 526/BISCOITO"},
                                        {"color_code": "83983", "color_desc": "CREME 985/BISCOITO"},
                                        {"color_code": "101741", "color_desc": "CONHAQUE 1273/PRETO"},
                                    ],
                                },
                                {
                                    "item_code": "82009",
                                    "item_desc": "METAL GLAMOUR PU 0.9 (V P/V 16/17)+POLINYLON 11HM2L+FORRO MILANO",
                                    "colors": [{"color_code": "47985", "color_desc": "OURO ROSADO/BRANCO 15/NATURAL"}],
                                },
                                {
                                    "item_code": "82290",
                                    "item_desc": "GLITER MINI SHINE(M/MK O/I 17)",
                                    "colors": [{"color_code": "23146", "color_desc": "OURO ROSADO"}],
                                },
                                {
                                    "item_code": "89072",
                                    "item_desc": "METALIZADO PREMIUM PU 0.9 (M/MD P/V 17/18)",
                                    "colors": [{"color_code": "74941", "color_desc": "GRAFITE 914"}],
                                },
                                {
                                    "item_code": "94157",
                                    "item_desc": "NAPA TURIM PU 1.0+VELVET",
                                    "colors": [{"color_code": "103313", "color_desc": "CHERRY 1335/CREME 516"}],
                                },
                                {
                                    "item_code": "108850",
                                    "item_desc": "NAPA PELE STRECH PU 1.0 (MD O/I 2019)",
                                    "colors": [
                                        {"color_code": "15745", "color_desc": "PRETO 01"},
                                        {"color_code": "35312", "color_desc": "BRANCO OFF 526"},
                                        {"color_code": "52531", "color_desc": "NUDE 658"},
                                        {"color_code": "83517", "color_desc": "CREME 985"},
                                    ],
                                },
                                {
                                    "item_code": "133274",
                                    "item_desc": "NOBUCK NEW 0.8+POLINYLON 11HML 2L+FORRO MILANO",
                                    "colors": [{"color_code": "16143", "color_desc": "PRETO 01/PRETO 01/PRETO 01"}],
                                },
                                {
                                    "item_code": "174390",
                                    "item_desc": "NOBUCK LISBOA PU 0.9 ( V PV 24/25)+BASE FLEX 1.0",
                                    "colors": [
                                        {"color_code": "29515", "color_desc": "BEGE 435/BISCOITO"},
                                        {"color_code": "101741", "color_desc": "CONHAQUE"},
                                    ],
                                },
                                {
                                    "item_code": "185819",
                                    "item_desc": "VERNIZ PREMIUM MARMORIZADO GLAM PU 1.0 (B P/V 24/25)+BASE FLEX",
                                    "colors": [{"color_code": "15787", "color_desc": "PRETO 01/PRETO"}],
                                },
                            ],
                            "summary": {"total_items": 29, "total_colors": 49},
                            "outputs": {
                                "json": "https://parser.n8n.marketcodebrasil.com.br/files/1743709282597_falta_20260222174602_parsed.json",
                                "csv": "https://parser.n8n.marketcodebrasil.com.br/files/1743709282597_falta_20260222174602_parsed.csv",
                                "html": "https://parser.n8n.marketcodebrasil.com.br/files/1743709282597_falta_20260222174602_parsed.html",
                                "json_local_path": "/app/output/1743709282597_falta_20260222174602_parsed.json",
                                "csv_local_path": "/app/output/1743709282597_falta_20260222174602_parsed.csv",
                                "html_local_path": "/app/output/1743709282597_falta_20260222174602_parsed.html",
                            },
                        },
                    },
                }
            }
        },
    },
    400: {
        "description": "Arquivo invalido (nao PDF).",
        "content": {
            "application/json": {
                "examples": {
                    "arquivo_nao_pdf": {
                        "summary": "Validacao de extensao",
                        "value": {"detail": "Envie um arquivo PDF"},
                    }
                }
            }
        },
    },
    500: {
        "description": "Falha interna durante o parse do PDF.",
        "content": {
            "application/json": {
                "examples": {
                    "erro_parse": {
                        "summary": "Falha ao processar",
                        "value": {"detail": "Falha ao processar PDF: erro ao ler tabela da pagina 2"},
                    }
                }
            }
        },
    },
}

DOWNLOAD_FILE_RESPONSES = {
    200: {"description": "Arquivo retornado com sucesso (JSON/CSV/HTML/binario)."},
    400: {
        "description": "Caminho de arquivo invalido.",
        "content": {
            "application/json": {
                "examples": {
                    "arquivo_invalido": {
                        "summary": "Tentativa de path traversal",
                        "value": {"detail": "Arquivo invalido"},
                    }
                }
            }
        },
    },
    404: {
        "description": "Arquivo nao encontrado.",
        "content": {
            "application/json": {
                "examples": {
                    "nao_encontrado": {
                        "summary": "Arquivo inexistente",
                        "value": {"detail": "Arquivo nao encontrado"},
                    }
                }
            }
        },
    },
}
