#!/usr/bin/env python3
"""Clipping Diário - Prefeitura de Anápolis.
Lê portais locais via RSS e Google Notícias, contorna bloqueios antibot/Cloudflare,
extrai o conteúdo principal das matérias e gera relatórios em DOCX, HTML e TXT.
"""

import argparse
import base64
import html
import json
import random
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

# Tenta carregar bibliotecas contra bloqueios e para raspagem avançada de HTML
try:
    from curl_cffi import requests as cffi_requests
    CFFI_DISPONIVEL = True
except ImportError:
    CFFI_DISPONIVEL = False

try:
    from bs4 import BeautifulSoup
    BS4_DISPONIVEL = True
except ImportError:
    BS4_DISPONIVEL = False

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    TZ = timezone(timedelta(hours=-3))

BASE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
GNEWS = "https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"


def calcular_limite(agora, hora_corte="11:30"):
    """Retorna o instante de corte: 11:30 do dia anterior a 'agora'."""
    h, m = (int(x) for x in hora_corte.split(":"))
    ontem = (agora - timedelta(days=1)).date()
    return datetime(ontem.year, ontem.month, ontem.day, h, m, tzinfo=TZ)


def sem_acento(t):
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def chave(t):
    return re.sub(r"[^a-z0-9 ]", " ", sem_acento(t))


def limpar(t):
    t = re.sub(r"<[^>]+>", " ", t or "")
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def baixar(url):
    """Acessa a página simulando um navegador real vindo de uma busca do Google."""
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.google.com/",
        "DNT": "1"
    }

    if CFFI_DISPONIVEL:
        try:
            res = cffi_requests.get(
                url,
                impersonate="chrome120",
                headers=headers,
                timeout=25,
                follow_redirects=True
            )
            return res.content, str(res.url)
        except Exception:
            pass

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read(), r.geturl()


def data_de(texto):
    for padrao in (r'"datePublished"\s*:\s*"([^"]+)"',
                   r'property="article:published_time"\s+content="([^"]+)"',
                   r'content="([^"]+)"\s+property="article:published_time"',
                   r'<time[^>]+datetime="([^"]+)"'):
        m = re.search(padrao, texto, re.I)
        if m:
            try:
                d = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
                return d.astimezone(TZ) if d.tzinfo else d.replace(tzinfo=TZ)
            except ValueError:
                continue
    return None


def extrair(url, cfg):
    """Abre a matéria e extrai título, data e os parágrafos do texto."""
    try:
        bruto, final_url = baixar(url)
        texto_html = bruto.decode("utf-8", "ignore")
    except Exception:
        return "", None, []

    titulo = ""
    paragrafos = []
    data_pub = data_de(texto_html)
    limite_p = cfg.get("paragrafos", 0)

    if BS4_DISPONIVEL:
        soup = BeautifulSoup(texto_html, "html.parser")

        # Extração do Título
        og_title = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "og:title"})
        if og_title and og_title.get("content"):
            titulo = limpar(og_title["content"])
        elif soup.find("h1"):
            titulo = limpar(soup.find("h1").get_text())
        elif soup.find("title"):
            titulo = limpar(soup.find("title").get_text())

        # Limpeza de elementos ruidosos
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "figcaption", "iframe", "noscript"]):
            tag.decompose()

        corpo = (soup.find("article") or
                 soup.find("main") or
                 soup.find("div", class_=re.compile(r"entry-content|post-content|content-text|materia|td-post-content|texto|article-body|post-body", re.I)) or
                 soup)

        ruido = [sem_acento(r) for r in cfg.get("ruido", [])]

        # 1. Tenta buscar marcas de parágrafo <p>
        elementos = corpo.find_all("p")

        # 2. Se o site não usar <p>, busca blocos de div/section com texto
        if not elementos or len(elementos) < 2:
            elementos = corpo.find_all(["div", "section"])

        for el in elementos:
            p_limpo = limpar(el.get_text())
            if len(p_limpo) < 50 or p_limpo.endswith(":"):
                continue
            if sem_acento(p_limpo) == sem_acento(titulo):
                continue
            if any(r in sem_acento(p_limpo) for r in ruido):
                continue
            if p_limpo not in paragrafos:
                paragrafos.append(p_limpo)
            if limite_p > 0 and len(paragrafos) >= limite_p:
                break
    else:
        # Extração fallback via Regex
        limpo = re.sub(r"<(script|style|nav|header|footer|aside|form)[^>]*>.*?</\1>", " ", texto_html, flags=re.S | re.I)
        m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', limpo, re.I)
        titulo = limpar(m.group(1)) if m else ""

        ruido = [sem_acento(r) for r in cfg.get("ruido", [])]
        for p in re.findall(r"<(?:p|div)[^>]*>(.*?)</(?:p|div)>", limpo, re.S | re.I):
            p_limpo = limpar(p)
            if len(p_limpo) < 50 or p_limpo.endswith(":") or any(r in sem_acento(p_limpo) for r in ruido):
                continue
            if sem_acento(p_limpo) == sem_acento(titulo):
                continue
            if p_limpo not in paragrafos:
                paragrafos.append(p_limpo)
            if limite_p > 0 and len(paragrafos) >= limite_p:
                break

    titulo = re.sub(r"\s*[|–-]\s*[^|–-]{0,40}$", "", titulo).strip()
    return titulo, data_pub, paragrafos


def link_real(url):
    """Resolve o link real de redirecionamento do Google Notícias."""
    if "news.google.com" not in url:
        return url
    m = re.search(r"/articles/([A-Za-z0-9_\-]+)", url)
    if m:
        try:
            bruto = base64.urlsafe_b64decode(m.group(1) + "===").decode("latin-1")
            alvo = re.search(r"https?://[^\s\x00-\x1f\"']+", bruto)
            if alvo:
                return alvo.group(0)
        except Exception:
            pass
    try:
        return baixar(url)[1]
    except Exception:
        return url


def ler_rss(url):
    try:
        bruto, _ = baixar(url)
        raiz = ET.fromstring(bruto)
    except Exception as e:
        return [], str(e)
    itens = []
    for it in raiz.iter("item"):
        link = (it.findtext("link") or "").strip()
        if not link:
            continue
        try:
            data = parsedate_to_datetime(it.findtext("pubDate")).astimezone(TZ)
        except Exception:
            data = None
        itens.append({
            "titulo": limpar(it.findtext("title")),
            "link": link,
            "data": data,
            "resumo": limpar(it.findtext("description")),
            "fonte_rss": limpar(it.findtext("source"))
        })
    return itens, None


def casa(texto, palavras):
    alvo = chave(texto)
    return [p for p in palavras if chave(p) in alvo]


def coletar(cfg, agora, limite):
    palavras = cfg["palavras_chave"]
    vistos, materias, status = set(), [], []

    def processar(link, fonte, data_rss, titulo_rss, resumo_rss, termos, sempre):
        link = link_real(link)
        base = re.sub(r"[?#].*$", "", link).rstrip("/")
        if base in vistos:
            return
        vistos.add(base)
        aviso = ""
        try:
            titulo, data, paragrafos = extrair(link, cfg)
        except Exception as e:
            titulo, data, paragrafos = "", None, []
            aviso = f"página bloqueou o acesso ({e})"

        # Se não extraiu o corpo da página, tenta o resumo do RSS
        if not paragrafos and resumo_rss:
            resumo_limpo = limpar(resumo_rss)
            if len(resumo_limpo) > 50 and sem_acento(resumo_limpo) != sem_acento(titulo_rss):
                paragrafos = [resumo_limpo]
                aviso = "usou resumo do RSS"

        data = data or data_rss or datetime.now(TZ)
        if data < limite:
            return

        titulo = titulo or titulo_rss

        # Descarta matérias sem título ou sem texto para não enviar vazias
        if not titulo or not paragrafos:
            return

        if not sempre and not termos:
            termos = casa(titulo + " " + " ".join(paragrafos), palavras)
            if not termos:
                return

        materias.append({
            "fonte": fonte,
            "titulo": titulo,
            "data": data,
            "paragrafos": paragrafos,
            "link": link,
            "termos": termos or casa(titulo, palavras)
        })
        marca = f"  [resumo RSS: {aviso}]" if aviso else ""
        print(f"    + {titulo[:70]}{marca}")

    for fonte in cfg["fontes"]:
        print(f"\n[{fonte['nome']}]")
        itens, erro = ler_rss(fonte["rss"])
        antes = len(materias)
        if erro:
            print(f"    ! feed indisponível ({erro})")
        for it in itens:
            if it["data"] and it["data"] < limite:
                continue
            sempre = fonte.get("sempre", False)
            termos = casa(it["titulo"] + " " + it["resumo"], palavras)
            generico = "anapolis" in chave(it["titulo"] + it["resumo"])
            if not sempre and not termos and not generico:
                continue
            if not termos and generico:
                termos = ["Anápolis"]
            processar(it["link"], fonte["nome"], it["data"], it["titulo"], it["resumo"], termos, sempre)
            time.sleep(random.uniform(0.5, cfg.get("pausa", 1.5)))

        status.append({
            "nome": fonte["nome"],
            "prioritaria": fonte.get("prioritaria", False),
            "erro": erro,
            "qtd": len(materias) - antes
        })

    if cfg.get("usar_google_news"):
        horas_gnews = max(1, int((agora - limite).total_seconds() // 3600) + 1)
        print("\n[Google Notícias]")
        for palavra in palavras:
            q = urllib.parse.quote(f'"{palavra}" when:{horas_gnews}h')
            achados, _ = ler_rss(GNEWS.format(q=q))
            for it in achados:
                fonte = it["fonte_rss"] or "Web"
                titulo = re.sub(r"\s+-\s+" + re.escape(fonte) + r"$", "", it["titulo"])
                processar(it["link"], fonte, it["data"], titulo, it["resumo"], [palavra], False)
            time.sleep(random.uniform(1.0, 2.5))

    materias.sort(key=lambda m: m["data"], reverse=True)
    materias = juntar_duplicadas(materias)
    return materias, status


def palavras_significativas(titulo, paragrafos=None):
    texto = titulo + " " + " ".join((paragrafos or [])[:1])
    return {w for w in chave(texto).split() if len(w) > 3}


def juntar_duplicadas(materias):
    """Agrupa notícias idênticas publicadas por diferentes veículos."""
    restantes = list(materias)
    finais = []
    while restantes:
        principal = restantes.pop(0)
        palavras_p = palavras_significativas(principal["titulo"], principal["paragrafos"])
        grupo, sobra = [], []
        for m in restantes:
            palavras_m = palavras_significativas(m["titulo"], m["paragrafos"])
            uniao = palavras_p | palavras_m
            intersecao = palavras_p & palavras_m
            parecido = len(intersecao) >= 4 and uniao and (len(intersecao) / len(uniao)) >= 0.4
            if parecido and abs((m["data"] - principal["data"]).total_seconds()) < 60 * 60 * 30:
                grupo.append(m)
            else:
                sobra.append(m)
        restantes = sobra
        if grupo:
            candidatos = [principal] + grupo
            principal = max(candidatos, key=lambda m: sum(len(p) for p in m["paragrafos"]))
            outras = [m for m in candidatos if m is not principal]
            principal = dict(principal)
            principal["duplicatas"] = [{"fonte": m["fonte"], "link": m["link"]} for m in outras]
        else:
            principal = dict(principal, duplicatas=[])
        finais.append(principal)
    finais.sort(key=lambda m: m["data"], reverse=True)
    return finais


def gerar_txt(cfg, materias, destino, agora):
    partes = [
        "PREFEITURA DE ANÁPOLIS",
        "SEMPRE DO SEU LADO",
        "",
        cfg.get("titulo", "Clipping de Notícias - Prefeitura de Anápolis"),
        agora.strftime("%d/%m/%Y"),
        "",
        "Palavras chave:"
    ]
    for p in cfg.get("palavras_chave", []):
        partes.append(f"  - {p}")
    partes.append("")
    partes.append("-" * 50)
    partes.append("")

    for m in materias:
        partes.append(f"[{m['fonte'].strip()}] {m['titulo'].rstrip('.')}.")
        partes.append(m["data"].strftime("%d/%m/%Y"))
        for p in m["paragrafos"]:
            partes.append(p)
        partes.append(m["link"])
        for d in m.get("duplicatas", []):
            partes.append(f"Também em: [{d['fonte']}] {d['link']}")
        partes.append("")

    destino.write_text("\n".join(partes), encoding="utf-8")
    return destino


def gerar_html(cfg, materias, destino, agora):
    css = (
        "body { font-family: Arial, sans-serif; max-width: 800px; margin: 30px auto; padding: 0 24px; line-height: 1.5; color: #222; } "
        ".header-branding { margin-bottom: 24px; } "
        ".header-branding .prefeitura { font-size: 18px; font-weight: bold; color: #003366; text-transform: uppercase; margin: 0; } "
        ".header-branding .anapolis { font-size: 26px; font-weight: bold; color: #003366; text-transform: uppercase; margin: 0; } "
        ".header-branding .slogan { font-size: 12px; font-weight: bold; color: #0066cc; text-transform: uppercase; margin-top: 2px; } "
        "h1 { font-size: 20px; font-weight: bold; color: #111; margin: 20px 0 4px; } "
        ".data-doc { font-size: 14px; color: #555; margin-bottom: 24px; } "
        ".keywords-box { background: #f8f9fa; border: 1px solid #e2e8f0; border-radius: 6px; padding: 16px; margin-bottom: 30px; } "
        ".keywords-box h3 { margin: 0 0 10px 0; font-size: 14px; font-weight: bold; } "
        ".keywords-list { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 16px; font-size: 13px; color: #444; } "
        ".materia { margin-bottom: 28px; } "
        ".materia .titulo { font-weight: bold; font-size: 15px; color: #111; margin-bottom: 2px; } "
        ".materia .data { font-size: 13px; color: #666; margin-bottom: 8px; } "
        ".materia p { font-size: 14px; margin: 0 0 8px 0; text-align: justify; } "
        ".materia .link a { color: #0b4f9e; text-decoration: underline; font-size: 13px; word-break: break-all; } "
        ".materia .dup { font-size: 12px; color: #555; margin-top: 4px; }"
    )
    p = [
        "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='utf-8'>",
        f"<title>{html.escape(cfg.get('titulo', 'Clipping de Notícias'))}</title>",
        f"<style>{css}</style></head><body>",
        "<div class='header-branding'>",
        "<div class='prefeitura'>PREFEITURA DE</div>",
        "<div class='anapolis'>ANÁPOLIS</div>",
        "<div class='slogan'>SEMPRE DO SEU LADO</div></div>",
        f"<h1>{html.escape(cfg.get('titulo', 'Clipping de Notícias - Prefeitura de Anápolis'))}</h1>",
        f"<div class='data-doc'>{agora:%d/%m/%Y}</div>",
        "<div class='keywords-box'><h3>Palavras chave:</h3><div class='keywords-list'>"
    ]

    for kw in cfg.get("palavras_chave", []):
        p.append(f"<div>{html.escape(kw)}</div>")

    p.append("</div></div>")

    for m in materias:
        dups = "".join(
            f"<div class='dup'>Também em: [{html.escape(d['fonte'])}] "
            f"<a href='{html.escape(d['link'])}'>{html.escape(d['link'])}</a></div>"
            for d in m.get("duplicatas", [])
        )
        p.append(
            "<div class='materia'>"
            f"<div class='titulo'>[{html.escape(m['fonte'].strip())}] {html.escape(m['titulo'].rstrip('.'))}.</div>"
            f"<div class='data'>{m['data']:%d/%m/%Y}</div>"
            + "".join(f"<p>{html.escape(x)}</p>" for x in m["paragrafos"])
            + f"<div class='link'><a href='{html.escape(m['link'])}'>{html.escape(m['link'])}</a></div>"
            + dups
            + "</div>"
        )
    p.append("</body></html>")
    destino.write_text("\n".join(p), encoding="utf-8")
    return destino


def gerar_docx(cfg, materias, destino, agora):
    """Gera o arquivo .docx idêntico ao modelo oficial da Prefeitura."""
    try:
        from docx import Document
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.shared import Pt, RGBColor, Inches
    except ImportError:
        print("! python-docx não instalado - arquivo Word não gerado")
        return None

    doc = Document()
    for section in doc.sections:
        section.top_margin = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(0.8)
        section.right_margin = Inches(0.8)

    normal_style = doc.styles["Normal"]
    normal_style.font.name = "Arial"
    normal_style.font.size = Pt(10.5)

    # Header / Branding
    p_brand = doc.add_paragraph()
    p_brand.paragraph_format.space_before = Pt(0)
    p_brand.paragraph_format.space_after = Pt(0)
    p_brand.paragraph_format.line_spacing = 1.05

    r1 = p_brand.add_run("PREFEITURA DE\n")
    r1.bold = True
    r1.font.size = Pt(14)
    r1.font.color.rgb = RGBColor(0x00, 0x33, 0x66)

    r2 = p_brand.add_run("ANÁPOLIS\n")
    r2.bold = True
    r2.font.size = Pt(20)
    r2.font.color.rgb = RGBColor(0x00, 0x33, 0x66)

    r3 = p_brand.add_run("SEMPRE DO SEU LADO")
    r3.bold = True
    r3.font.size = Pt(9.5)
    r3.font.color.rgb = RGBColor(0x00, 0x66, 0xCC)

    # Título do Documento e Data
    p_title = doc.add_paragraph()
    p_title.paragraph_format.space_before = Pt(14)
    p_title.paragraph_format.space_after = Pt(2)
    rt = p_title.add_run(cfg.get("titulo", "Clipping de Notícias - Prefeitura de Anápolis"))
    rt.bold = True
    rt.font.size = Pt(15)
    rt.font.color.rgb = RGBColor(0x11, 0x11, 0x11)

    p_date = doc.add_paragraph()
    p_date.paragraph_format.space_before = Pt(0)
    p_date.paragraph_format.space_after = Pt(14)
    rd = p_date.add_run(agora.strftime("%d/%m/%Y"))
    rd.font.size = Pt(10.5)
    rd.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    # Tabela de Palavras-chave
    keywords = cfg.get("palavras_chave", [])
    if keywords:
        p_kw = doc.add_paragraph()
        p_kw.paragraph_format.space_before = Pt(4)
        p_kw.paragraph_format.space_after = Pt(4)
        rk = p_kw.add_run("Palavras chave:")
        rk.bold = True
        rk.font.size = Pt(11)

        table = doc.add_table(rows=0, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False

        for i in range(0, len(keywords), 2):
            row = table.add_row().cells
            pl = row[0].paragraphs[0]
            pl.paragraph_format.space_before = Pt(1)
            pl.paragraph_format.space_after = Pt(1)
            rl = pl.add_run(keywords[i])
            rl.font.size = Pt(9.5)
            rl.font.color.rgb = RGBColor(0x44, 0x44, 0x44)

            if i + 1 < len(keywords):
                pr = row[1].paragraphs[0]
                pr.paragraph_format.space_before = Pt(1)
                pr.paragraph_format.space_after = Pt(1)
                rr = pr.add_run(keywords[i + 1])
                rr.font.size = Pt(9.5)
                rr.font.color.rgb = RGBColor(0x44, 0x44, 0x44)

    p_space = doc.add_paragraph()
    p_space.paragraph_format.space_before = Pt(12)
    p_space.paragraph_format.space_after = Pt(6)

    # Matérias
    for m in materias:
        p_t = doc.add_paragraph()
        p_t.paragraph_format.space_before = Pt(10)
        p_t.paragraph_format.space_after = Pt(2)
        p_t.paragraph_format.line_spacing = 1.15
        rt = p_t.add_run(f"[{m['fonte'].strip()}] {m['titulo'].rstrip('.')}.")
        rt.bold = True
        rt.font.size = Pt(11)

        p_d = doc.add_paragraph()
        p_d.paragraph_format.space_before = Pt(0)
        p_d.paragraph_format.space_after = Pt(4)
        rd = p_d.add_run(m["data"].strftime("%d/%m/%Y"))
        rd.font.size = Pt(9.5)
        rd.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

        for p_text in m["paragrafos"]:
            p_b = doc.add_paragraph()
            p_b.paragraph_format.space_before = Pt(0)
            p_b.paragraph_format.space_after = Pt(5)
            p_b.paragraph_format.line_spacing = 1.15
            rb = p_b.add_run(p_text)
            rb.font.size = Pt(10)

        p_l = doc.add_paragraph()
        p_l.paragraph_format.space_before = Pt(2)
        p_l.paragraph_format.space_after = Pt(12)
        rl = p_l.add_run(m["link"])
        rl.font.size = Pt(9.5)
        rl.font.color.rgb = RGBColor(0x0B, 0x4F, 0x9E)
        rl.underline = True

        for d in m.get("duplicatas", []):
            p_dup = doc.add_paragraph()
            p_dup.paragraph_format.space_before = Pt(0)
            p_dup.paragraph_format.space_after = Pt(4)
            rdup = p_dup.add_run(f"Também em: [{d['fonte']}] {d['link']}")
            rdup.font.size = Pt(9)
            rdup.font.color.rgb = RGBColor(0x60, 0x60, 0x60)

    doc.save(destino)
    return destino


def main():
    ap = argparse.ArgumentParser(description="Clipping diário - Prefeitura de Anápolis")
    ap.add_argument("--horas", type=int, help="janela fixa em horas (substitui o corte de 11:30)")
    ap.add_argument("--hora-corte", default=None, help="horário de corte do dia anterior, ex: 11:30")
    ap.add_argument("--paragrafos", type=int)
    ap.add_argument("--config", default=str(BASE / "config.json"))
    ap.add_argument("--saida", default=str(BASE / "clippings"))
    a = ap.parse_args()

    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    if a.paragrafos is not None:
        cfg["paragrafos"] = a.paragrafos
    agora = datetime.now(TZ)
    pasta = Path(a.saida)
    pasta.mkdir(parents=True, exist_ok=True)

    if a.horas:
        limite = agora - timedelta(hours=a.horas)
    else:
        limite = calcular_limite(agora, a.hora_corte or cfg.get("hora_corte", "11:30"))

    print(f"Buscando matérias de {limite:%d/%m %H:%M} até {agora:%d/%m %H:%M}")

    materias, status = coletar(cfg, agora, limite)
    nome = f"clipping-{agora:%Y-%m-%d}"

    arquivos = [
        gerar_docx(cfg, materias, pasta / f"{nome}.docx", agora),
        gerar_html(cfg, materias, pasta / f"{nome}.html", agora),
        gerar_txt(cfg, materias, pasta / f"{nome}.txt", agora)
    ]

    problemas = [s for s in status if s["prioritaria"] and (s["erro"] or s["qtd"] == 0)]
    print(f"\n{'=' * 55}")
    if problemas:
        print("!!! ATENÇÃO - fonte(s) prioritária(s) sem matérias hoje:")
        for s in problemas:
            motivo = s["erro"] or "feed leu certo mas nada bateu com as palavras-chave"
            print(f"    - {s['nome']}: {motivo}")
    else:
        print("Todas as fontes prioritárias trouxeram matérias normalmente.")

    print(f"\n{len(materias)} matéria(s) coletadas")
    for f in arquivos:
        if f:
            print(f"  -> {f}")


if __name__ == "__main__":
    main()