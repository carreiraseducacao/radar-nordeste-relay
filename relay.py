#!/usr/bin/env python3
"""
Radar Nordeste — RELAY (GitHub Actions).
Busca, de um IP fora do Hostinger, as fontes que bloqueiam o servidor do radar
e publica tudo em data/relay.json (consumido por fontes/relay.php no Hostinger).

Fontes:
  famem     Diário Oficial dos Municípios do MA (Siganet bloqueia o Hostinger):
            baixa as edições novas, extrai texto (pdftotext) e guarda JANELAS de
            texto em volta de palavras-chave de concurso (o classificador roda no PHP).
  comperve  Núcleo de concursos da UFRN: lista de concursos + feed de notícias da home.
  aocp      Instituto AOCP (Cloudflare costuma dar 403 em datacenter — best effort).
"""
import json, re, os, sys, time, html, subprocess, urllib.request, urllib.error
from datetime import datetime, timezone

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "relay.json")
STATE = os.path.join(HERE, "data", "relay_state.json")
KEYS = ["CONCURSO PÚBLICO", "CONCURSO PUBLICO", "SELEÇÃO PÚBLICA", "BANCA ORGANIZADORA", "COMISSÃO ORGANIZADORA",
        "COMISSÃO DO CONCURSO", "CRIA CARGOS", "CRIAÇÃO DE CARGOS", "AUTORIZA A REALIZAÇÃO DE CONCURSO"]

import ssl
_CTX = ssl.create_default_context(); _CTX.check_hostname = False; _CTX.verify_mode = ssl.CERT_NONE   # sites .br com cadeia incompleta
def get(url, timeout=40, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: data = r.read()
    except urllib.error.URLError as e:
        if "CERTIFICATE" not in str(e): raise
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r: data = r.read()
    if binary: return data
    for enc in ("utf-8", "iso-8859-1"):
        try: return data.decode(enc)
        except UnicodeDecodeError: pass
    return data.decode("utf-8", "replace")

def log(*a): print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)

def load(path, default):
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except Exception: return default

# ---------------------------------------------------------------- FAMEM / MA
def famem(state):
    out = {"ok": False, "edicoes": []}
    try:
        h = get("http://diariooficial.famem.org.br/", 60)
    except Exception as e:
        log("famem home falhou:", e); return out
    links = {}
    for m in re.finditer(r'https://painel\.siganet\.net\.br/upload/[^"\'\s]*edicao-(\d+)-assinado\.pdf', h, re.I):
        links[int(m.group(1))] = m.group(0)
    if not links:
        log("famem: nenhum link de edição"); return out
    last = int(state.get("famem_last", 0))
    novas = sorted(e for e in links if e > last)[-3:]   # no máx. 3 edições por rodada
    if not novas and last == 0: novas = [max(links)]      # 1ª execução: só a mais recente
    for ed in novas:
        url = links[ed]
        try:
            pdf = get(url, 180, binary=True)
            pdfpath = f"/tmp/famem_{ed}.pdf"; open(pdfpath, "wb").write(pdf)
            txt = subprocess.run(["pdftotext", pdfpath, "-"], capture_output=True, text=True, timeout=240).stdout
        except Exception as e:
            log(f"famem ed {ed} falhou:", e); continue
        T = re.sub(r"[ \t]+", " ", txt)
        U = T.upper()
        pos = []
        for k in KEYS:
            i = 0
            while True:
                i = U.find(k, i)
                if i < 0: break
                pos.append(i); i += len(k)
        pos.sort()
        janelas = []; ult = -10**9
        for p in pos:
            if p - ult < 2500: continue          # funde janelas próximas
            ult = p
            janelas.append(T[max(0, p - 1500): p + 3200])
            if len(janelas) >= 40: break
        out["edicoes"].append({"edicao": ed, "url": url, "data": datetime.now().strftime("%Y-%m-%d"),
                               "chars": len(T), "janelas": janelas})
        log(f"famem ed {ed}: {len(T)} chars, {len(janelas)} janela(s)")
        state["famem_last"] = max(last, ed)
    out["ok"] = True
    return out

# ---------------------------------------------------------------- COMPERVE / RN
def comperve(state):
    out = {"ok": False, "entries": [], "news": []}
    try:
        h = get("https://www.comperve.ufrn.br/conteudo/concursos.php", 40)
        seen = set()
        for m in re.finditer(r'<a[^>]+href=["\']([^"\']*?concursos/([^"\']+?)/informacoes\.php[^"\']*)["\'][^>]*>(.*?)</a>', h, re.S | re.I):
            t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(3)))).strip()
            slug = m.group(2).strip("/").replace("/", "_")
            if not t or slug in seen: continue
            seen.add(slug)
            l = m.group(1)
            link = l if l.startswith("http") else ("https://www.comperve.ufrn.br" + (l if l.startswith("/") else "/conteudo/" + l))
            out["entries"].append({"id": slug, "titulo": t[:220], "link": link})
        out["ok"] = True
        log("comperve: entradas", len(out["entries"]))
    except Exception as e:
        log("comperve lista falhou:", e)
    try:
        h = get("https://www.comperve.ufrn.br/", 40)
        # <strong class="titulo_noticia">DD/MM/AAAA HH:MM - TÍTULO</strong> <p class="texto_noticia"><a href="LINK">TEXTO</a></p>
        for m in re.finditer(r'<strong[^>]*titulo_noticia[^>]*>\s*(\d{2}/\d{2}/\d{4}) \d{2}:\d{2} -\s*(.*?)</strong>\s*<p[^>]*>\s*(?:<a[^>]+href="([^"]*)"[^>]*>)?(.*?)(?:</a>)?\s*</p>', h, re.S | re.I):
            tit = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
            txt = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(4)))).strip()
            link = m.group(3) or ""
            if link and not link.startswith("http"): link = "https://www.comperve.ufrn.br" + link
            out["news"].append({"data": m.group(1), "titulo": tit[:200], "texto": txt[:500], "link": link})
            if len(out["news"]) >= 40: break
        log("comperve: notícias", len(out["news"]))
    except Exception as e:
        log("comperve home falhou:", e)
    return out

# ---------------------------------------------------------------- AOCP (API JSON descoberta em 20/09/2026)
NE_RE = re.compile(r"\b(CE|PE|PB|RN|AL|BA|MA|PI|SE)\b|CEAR[ÁA]|PERNAMBUC|PARA[ÍI]BA|RIO GRANDE DO NORTE|ALAGOAS|BAHIA|MARANH|PIAU|SERGIPE", re.I)
def aocp(state):
    out = {"ok": False, "entries": []}
    try:
        j = json.loads(get("https://www.institutoaocp.org.br/api/concursos", 40))
    except Exception as e:
        log("aocp api falhou:", e); return out
    for c in j if isinstance(j, list) else j.get("data", []):
        nome = c.get("nome") or ""; chamada = c.get("chamada") or ""
        if not NE_RE.search(nome + " " + chamada): continue
        out["entries"].append({"id": str(c.get("id")), "titulo": (nome + " — " + chamada)[:260],
                               "link": f"https://www.institutoaocp.org.br/concursos/{c.get('id')}",
                               "status": c.get("status"), "dataInscricao": c.get("dataInscricao"), "dataProva": c.get("dataProva"),
                               "vagas": c.get("vagas"), "tipo": c.get("tipoProcesso")})
    out["ok"] = True
    log("aocp: entradas NE", len(out["entries"]), "de", len(j) if isinstance(j, list) else "?")
    return out

# ---------------------------------------------------------------- PÁGINAS genéricas (sites que bloqueiam o Hostinger) — 23/09/2026
PAGINAS = [
    {"id": "seedf_home", "url": "https://www.educacao.df.gov.br/", "ente": "SEEDF", "label": "site SEEDF (concurso 10.604 vagas; banca em contratação)",
     "filtro": r"CONCURSO P[ÚU]BLICO|EDITAL[^.]{0,80}(PROFESSOR|CONCURSO)|BANCA (ORGANIZADORA|EXAMINADORA)|PROFESSOR[^.]{0,60}CONCURSO"},
    {"id": "seedf_concurso", "url": "https://www.educacao.df.gov.br/concurso-publico/", "ente": "SEEDF", "label": "SEEDF — página de concurso público",
     "filtro": r"CONCURSO|EDITAL|BANCA|INSCRI|PROFESSOR"},
]
def paginas(state):
    out = []
    for pg in PAGINAS:
        try:
            h = get(pg["url"], 40)
        except Exception as e:
            log(f"pagina {pg['id']} falhou:", e); out.append({**pg, "ok": False}); continue
        h2 = re.sub(r"<(script|style|nav|footer)\b.*?</\1>", " ", h, flags=re.S | re.I)
        txt = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h2))).strip()
        fr = []
        for f in re.split(r"(?<=[.!?;:])\s+", txt):
            f = f.strip()
            if len(f) > 12 and re.search(pg["filtro"], f, re.I): fr.append(f[:300])
        links = []
        for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', h, re.S | re.I):
            u = html.unescape(m.group(1)); rot = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
            if not re.search(r"\.pdf|edital|concurso|inscri", u + " " + rot, re.I): continue
            if not u.startswith("http"): u = re.sub(r"^(https?://[^/]+).*$", r"\1", pg["url"]) + (u if u.startswith("/") else "/" + u)
            links.append({"rot": rot[:150], "url": u})
            if len(links) >= 60: break
        out.append({**pg, "ok": True, "frases": list(dict.fromkeys(fr))[:200], "links": links})
        log(f"pagina {pg['id']}: {len(fr)} frase(s), {len(links)} link(s)")
    return out

def main():
    state = load(STATE, {})
    res = {"generated_at": datetime.now(timezone.utc).isoformat(), "famem": famem(state),
           "comperve": comperve(state), "aocp": aocp(state), "paginas": paginas(state)}
    # mantém as últimas 5 edições da FAMEM publicadas (o PHP marca o que já leu)
    prev = load(OUT, {})
    old = [e for e in prev.get("famem", {}).get("edicoes", []) if e["edicao"] not in {x["edicao"] for x in res["famem"]["edicoes"]}]
    res["famem"]["edicoes"] = sorted(old + res["famem"]["edicoes"], key=lambda x: x["edicao"])[-5:]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("relay.json:", os.path.getsize(OUT), "bytes")

if __name__ == "__main__":
    main()
