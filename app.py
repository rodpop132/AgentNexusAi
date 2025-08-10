# app.py — AWP Bot (Discord) — build “smart+pretty+logs”
# ------------------------------------------------------------
# Painéis:
#   /panel_awp      -> Comprar + "Como me tornar afiliado!" (Gestão só aparece se ADMIN publicar)
#   /panel_ticket   -> painel só com 🎫 Abrir Ticket
#   /panel_free     -> painel para abrir canal FREE (verificação por imagem do YouTube)
#   /panel_feedback -> painel de feedback
#
# Extras:
# - Logs com embeds e BOTÃO "Abrir canal" (checkout/free/afiliados)
# - IA com persona gigante (100+ linhas) e exemplos; tom humano, instrucional
# - Conversa natural no chat geral; menções sempre respondidas; anti-spam por usuário
# - Mensagem aleatória 1x/hora no chat
# - Categorias privadas (visíveis só pra Admin + cliente que abriu)
# - Botões "🔒 Fechar canal" (ticket/checkout/free) com confirmação
# - /purge (admin) para limpar mensagens
# - Presença: "Jogando NexusAPI melhor servidor para compra de executores!"
# - Checkout atualizado: Stripe R$ 40
# - Render-ready: usa PORT do ambiente e rota /health
# ------------------------------------------------------------

import os, re, sqlite3, logging, threading, asyncio, random, time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks

from dotenv import load_dotenv
import requests
from fastapi import FastAPI
import uvicorn

# =============== CONFIG ===============
load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID = int(os.getenv("GUILD_ID", "1403859583482728611"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "1271573735383760980"))

STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID", "0"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0"))
CHECKOUT_CATEGORY_ID = int(os.getenv("CHECKOUT_CATEGORY_ID", "0"))
FREE_CATEGORY_ID = int(os.getenv("FREE_CATEGORY_ID", "0"))

CHAT_CHANNEL_ID = int(os.getenv("CHAT_CHANNEL_ID", "1403862060940525640"))

ACCESS_ROLE_ID = int(os.getenv("ACCESS_ROLE_ID", "0"))          # acesso pago/aprovado
FREE_ROLE_ID   = int(os.getenv("FREE_ROLE_ID", "0"))            # acesso FREE por imagem YouTube
LOGS_CHANNEL_ID= int(os.getenv("LOGS_CHANNEL_ID", "0"))

# Produto / Checkout (R$ 40)
CHECKOUT_PRODUCT_NAME = os.getenv("CHECKOUT_PRODUCT_NAME", "AWP Plano Padrão")
CHECKOUT_LINK = os.getenv("CHECKOUT_LINK", "https://buy.stripe.com/aFacN53MX7GcfuUaW55EY02")

# IA (OpenRouter)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "mistralai/mistral-7b-instruct:free")
OPENROUTER_VISION_MODEL = os.getenv("OPENROUTER_VISION_MODEL", "qwen/qwen-2.5-vl-7b-instruct:free")
# tentar múltiplas bases para evitar 404
OR_BASES = [
    "https://openrouter.ai/api/v1/chat/completions",
    "https://openrouter.ai/api/v1/chat/completions/",
]

# Afiliados
AFFILIATE_BASE = os.getenv("AFFILIATE_BASE", "https://seu-site.com/awp?ref=")
AFFILIATE_ROLE_ID = int(os.getenv("AFFILIATE_ROLE_ID", "1404075826932355163"))

# Keys
KEYS_FILE = os.getenv("KEYS_FILE", "keys.txt")  # uma key por linha; se usada -> “KEY,USED:<user_id>:<ts>”

# Canais do YouTube aceitos para FREE
YOUTUBE_CHANNEL_NAMES = [s.strip() for s in os.getenv("YOUTUBE_CHANNEL_NAMES", "AWP Oficial,AWP Nexus").split(",") if s.strip()]

# Estilo
THEME_COLOR = int(os.getenv("THEME_COLOR", "0x5865F2"), 16) if str(os.getenv("THEME_COLOR","")).startswith("0x") else int(os.getenv("THEME_COLOR","5793266"))
EMOJI_STYLE = os.getenv("EMOJI_STYLE", "✨")

# Porta do Render (ou 8000 local)
PORT = int(os.getenv("PORT", "8000"))

INTENTS = discord.Intents.default()
INTENTS.members = True
INTENTS.message_content = True

logging.basicConfig(level=logging.INFO)
DB_PATH = "awp_bot.db"
GUILD_OBJ = discord.Object(id=GUILD_ID)
MENTION_RE = re.compile(r"<@!?(\d+)>")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent":"AWPBot/1.5"})
_key_file_lock = asyncio.Lock()

DEFAULT_INTEREST_WORDS = [
    "interessado","interesse","comprar","preço","quanto","quero",
    "como funciona","onde compro","pago","inscrição","adquirir","assinar",
    "teste grátis","free","acesso","liberar","print"
]

# =============== DB ===============
def db_conn(): return sqlite3.connect(DB_PATH, check_same_thread=False)

def db_init():
    con=db_conn(); cur=con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY, name TEXT, lang TEXT DEFAULT 'pt', registered_at TEXT);""")
    cur.execute("""CREATE TABLE IF NOT EXISTS affiliates(
        code TEXT PRIMARY KEY, owner_id INTEGER, created_at TEXT, clicks INTEGER DEFAULT 0,
        conversions INTEGER DEFAULT 0, revenue_cents INTEGER DEFAULT 0);""")
    cur.execute("""CREATE TABLE IF NOT EXISTS products(slug TEXT PRIMARY KEY, name TEXT);""")
    cur.execute("""CREATE TABLE IF NOT EXISTS settings(
        id INTEGER PRIMARY KEY CHECK(id=1),
        ai_chat_enabled INTEGER DEFAULT 1,
        ai_chat_interval INTEGER DEFAULT 60,
        chat_channel_id INTEGER,
        logs_channel_id INTEGER,
        theme_color INTEGER DEFAULT 5793266,
        emoji_style TEXT DEFAULT '✨',
        ai_enabled INTEGER DEFAULT 1,
        ai_model TEXT,
        ai_persona TEXT DEFAULT 'persona',
        autodm_enabled INTEGER DEFAULT 1,
        autodm_interval INTEGER DEFAULT 30,
        dm_keywords TEXT
    );""")
    cur.execute("""CREATE TABLE IF NOT EXISTS ai_optin(user_id INTEGER PRIMARY KEY);""")
    cur.execute("""CREATE TABLE IF NOT EXISTS dm_optin(user_id INTEGER PRIMARY KEY);""")
    cur.execute("INSERT OR IGNORE INTO products(slug,name) VALUES(?,?)", ("awp", CHECKOUT_PRODUCT_NAME))
    if not cur.execute("SELECT id FROM settings WHERE id=1").fetchone():
        cur.execute("""INSERT INTO settings(
            id, ai_chat_enabled, ai_chat_interval, chat_channel_id, logs_channel_id,
            theme_color, emoji_style, ai_enabled, ai_model, ai_persona,
            autodm_enabled, autodm_interval, dm_keywords
        ) VALUES(1, 1, 60, ?, 0, ?, ?, 1, ?, ?, 1, 30, ?)""",
        (CHAT_CHANNEL_ID, THEME_COLOR, EMOJI_STYLE, OPENROUTER_MODEL, _default_persona(), ",".join(DEFAULT_INTEREST_WORDS)))
    con.commit(); con.close()
    migrate_db()

def migrate_db():
    con=db_conn(); cur=con.cursor()
    cur.execute("PRAGMA table_info(settings)")
    cols={r[1] for r in cur.fetchall()}
    def add(col, ddl):
        if col not in cols:
            try: cur.execute(f"ALTER TABLE settings ADD COLUMN {ddl}"); logging.info(f"[DB] add {col}")
            except Exception as e: logging.warning(f"[DB] {col} fail: {e}")
    add("chat_channel_id","chat_channel_id INTEGER")
    add("logs_channel_id","logs_channel_id INTEGER")
    add("theme_color","theme_color INTEGER DEFAULT 5793266")
    add("emoji_style","emoji_style TEXT DEFAULT '✨'")
    add("ai_enabled","ai_enabled INTEGER DEFAULT 1")
    add("ai_model","ai_model TEXT")
    add("ai_persona","ai_persona TEXT DEFAULT 'persona'")
    add("autodm_enabled","autodm_enabled INTEGER DEFAULT 1")
    add("autodm_interval","autodm_interval INTEGER DEFAULT 30")
    add("dm_keywords","dm_keywords TEXT")
    con.commit(); con.close()

def db_exec(q,p=()): con=db_conn(); cur=con.cursor(); cur.execute(q,p); con.commit(); con.close()
def db_fetchone(q,p=()): con=db_conn(); cur=con.cursor(); cur.execute(q,p); r=cur.fetchone(); con.close(); return r
def db_fetchall(q,p=()): con=db_conn(); cur=con.cursor(); cur.execute(q,p); r=cur.fetchall(); con.close(); return r

def is_admin(uid:int)->bool: return uid==ADMIN_ID

def get_settings():
    try:
        row=db_fetchone("""SELECT
            ai_chat_enabled, ai_chat_interval, chat_channel_id, logs_channel_id,
            theme_color, emoji_style, ai_enabled, ai_model, ai_persona,
            autodm_enabled, autodm_interval, dm_keywords
        FROM settings WHERE id=1""")
    except sqlite3.OperationalError:
        migrate_db()
        row=db_fetchone("""SELECT
            ai_chat_enabled, ai_chat_interval, chat_channel_id, logs_channel_id,
            theme_color, emoji_style, ai_enabled, ai_model, ai_persona,
            autodm_enabled, autodm_interval, dm_keywords
        FROM settings WHERE id=1""")
    if not row:
        return (1,60,CHAT_CHANNEL_ID,0,THEME_COLOR,EMOJI_STYLE,1,OPENROUTER_MODEL,_default_persona(),1,30,",".join(DEFAULT_INTEREST_WORDS))
    return row

def set_ai_enabled(v:bool): db_exec("UPDATE settings SET ai_enabled=? WHERE id=1",(1 if v else 0,))
def set_ai_model(m:str): db_exec("UPDATE settings SET ai_model=? WHERE id=1",(m,))
def set_ai_chat_enabled(v:bool): db_exec("UPDATE settings SET ai_chat_enabled=? WHERE id=1",(1 if v else 0,))
def set_ai_chat_interval(m:int): db_exec("UPDATE settings SET ai_chat_interval=? WHERE id=1",(max(3,min(720,m)),))
def set_autodm_enabled(v:bool): db_exec("UPDATE settings SET autodm_enabled=? WHERE id=1",(1 if v else 0,))
def set_autodm_interval(m:int): db_exec("UPDATE settings SET autodm_interval=? WHERE id=1",(max(5,min(1440,m)),))

def get_dm_keywords()->List[str]:
    r=db_fetchone("SELECT dm_keywords FROM settings WHERE id=1")
    if not r or not r[0]: return list(DEFAULT_INTEREST_WORDS)
    return [w.strip().lower() for w in r[0].split(",") if w.strip()]
def add_dm_keyword(w:str):
    words=set(get_dm_keywords()); words.add(w.strip().lower())
    db_exec("UPDATE settings SET dm_keywords=? WHERE id=1",(",".join(sorted(words)),))
def rem_dm_keyword(w:str):
    words=[x for x in get_dm_keywords() if x!=w.strip().lower()]
    db_exec("UPDATE settings SET dm_keywords=? WHERE id=1",(",".join(words),))
def dm_add_optin(uid:int): db_exec("INSERT OR IGNORE INTO dm_optin(user_id) VALUES(?)",(uid,))
def dm_rem_optin(uid:int): db_exec("DELETE FROM dm_optin WHERE user_id=?",(uid,))
def dm_is_optin(uid:int)->bool: return db_fetchone("SELECT 1 FROM dm_optin WHERE user_id=?",(uid,)) is not None

# =============== LOGS HELPERS ===============
async def get_or_create_logs(guild: discord.Guild)->discord.TextChannel:
    global LOGS_CHANNEL_ID
    ch = guild.get_channel(LOGS_CHANNEL_ID) if LOGS_CHANNEL_ID else None
    if isinstance(ch, discord.TextChannel): return ch
    try:
        ch = await guild.create_text_channel("awp-logs")
        db_exec("UPDATE settings SET logs_channel_id=? WHERE id=1", (ch.id,))
        LOGS_CHANNEL_ID = ch.id
        return ch
    except:
        # último fallback: usa o primeiro canal de texto que achar
        for c in guild.text_channels:
            return c
    raise RuntimeError("Sem canal de logs disponível")

def channel_url(guild_id:int, channel_id:int)->str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}"

def make_link_view(guild_id:int, channel_id:int, label:str="Abrir canal")->discord.ui.View:
    v=discord.ui.View()
    v.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label=label, url=channel_url(guild_id, channel_id)))
    return v

def embed_desc(title:str, desc:str)->discord.Embed:
    e=discord.Embed(title=title, description=desc, color=get_settings()[4])
    e.set_footer(text="AWP • atendimento inteligente")
    return e

async def ai_short_desc(title:str)->str:
    persona=get_settings()[8]
    sys=persona + " Gere uma frase curta (até 2 linhas), convidativa, com 1 emoji."
    out=await aor_chat([{"role":"system","content":sys},{"role":"user","content":f"Título: {title}"}])
    if not out: return f"{title} — toque nos botões para começar! {EMOJI_STYLE}"
    return out.strip()

# =============== KEYS (AFILIADOS) ===============
def _ensure_keys_file():
    try:
        os.makedirs(os.path.dirname(os.path.abspath(KEYS_FILE)), exist_ok=True)
    except Exception: pass
    if not os.path.exists(KEYS_FILE):
        with open(KEYS_FILE,"w",encoding="utf-8") as f: f.write("")

async def redeem_key_for_user(user_id:int, key:str)->str:
    key=key.strip()
    if not key: return "Código vazio."
    async with _key_file_lock:
        def load():
            with open(KEYS_FILE,"r",encoding="utf-8",errors="ignore") as f:
                return [line.rstrip("\n") for line in f]
        def save(lines:List[str]):
            tmp=KEYS_FILE+".tmp"
            with open(tmp,"w",encoding="utf-8") as f: f.write("\n".join(lines).rstrip("\n"))
            os.replace(tmp, KEYS_FILE)
        lines=await asyncio.to_thread(load)
        idx=None; used=None
        for i,line in enumerate(lines):
            if not line.strip(): continue
            k=line.split(",",1)[0].strip()
            if k==key:
                idx=i
                if "," in line and "USED" in line.split(",",1)[1]:
                    used=True
                break
        if idx is None: return "❌ Código inválido."
        if used: return "⚠️ Este código já foi usado."
        ts=datetime.now(timezone.utc).isoformat()
        lines[idx]=f"{key},USED:{user_id}:{ts}"
        await asyncio.to_thread(save, lines)
        return "✅ Código válido! Cargo de afiliado liberado."

# =============== IA / PERSONA ===============
def _default_persona()->str:
    # >100 linhas de conhecimento para a IA se comportar como humano, entender fluxos, canais e políticas.
    return (
        "Você é o **Assistente AWP**.\n"
        "AWP = **executor para PC** (software legítimo). Este servidor vende acesso pago (via Stripe) e oferece acesso FREE (verificação por imagem do YouTube), além de programa de afiliados.\n"
        "Fale como humano: caloroso, direto, educado, profissional; 1 emoji quando fizer sentido. Evite jargão; responda em 1–3 linhas em público; detalhe quando pedirem.\n"
        "\n"
        "=== O QUE É O AWP ===\n"
        "• Produto: executor para PC, focado em estabilidade, desempenho e suporte.\n"
        "• Não é arma; não confunda com rifle AWP. Aqui AWP é **software**.\n"
        "• Requisitos: PC Windows, internet estável; seguir guias do servidor.\n"
        "\n"
        "=== PREÇO & PAGAMENTO ===\n"
        "• Preço atual: **R$ 40**.\n"
        "• Link oficial de pagamento (Stripe): {CHECKOUT_LINK}\n"
        "• Após pagar, o usuário **envia print do pagamento no canal de checkout** (imagem nítida). Se aprovado, recebe cargo de acesso.\n"
        "\n"
        "=== ACESSO FREE (YouTube por imagem) ===\n"
        "• Para liberar FREE, o usuário abre o painel FREE, cria um canal privado `free-<nome>` e **envia print** mostrando que está **Inscrito/Subscribed** em canal autorizado.\n"
        "• A imagem deve mostrar: botão **Inscrito/Subscribed** e **nome do canal** claramente visível.\n"
        "• Canais aceitos são configurados em `YOUTUBE_CHANNEL_NAMES`.\n"
        "• Se aprovado por visão, enviaremos DM informando sucesso e aplicaremos cargo FREE.\n"
        "• Se reprovado, enviaremos DM com motivo e orientação para refazer.\n"
        "\n"
        "=== CHECKOUT (ACESSO PAGO) ===\n"
        "• Passo a passo: (1) pagar no Stripe; (2) **enviar print do pagamento no canal de checkout**; (3) aguardar validação automática.\n"
        "• Se aprovado, o bot aplica o cargo de acesso e confirma. Se pendente ou falhar visão, encaminhamos para revisão humana.\n"
        "\n"
        "=== AFILIADOS ===\n"
        "• Quem já tiver **código** resgata com `/affiliate_redeem` para ganhar o cargo **AWP Affiliate**.\n"
        "• Como se tornar afiliado: use o botão **“Como me tornar afiliado!”** no painel AWP e siga as instruções (falar com staff, receber código, etc.).\n"
        "• Códigos são gerenciados em `keys.txt`; se usado, o bot marca `USED:<user_id>:<ts>`.\n"
        "\n"
        "=== CANAIS & PAINÉIS ===\n"
        "• `/panel_awp`: mostra **Comprar / Checkout** e **Como me tornar afiliado!**; quando um ADMIN publica, aparece também **Gestão (admin)** para ele.\n"
        "• `/panel_ticket`: painel com **Abrir Ticket** -> cria canal `ticket-<nome>` privado (Admin + autor).\n"
        "• `/panel_free`: painel para abrir o canal `free-<nome>` privado (Admin + autor) e enviar a print da inscrição do YouTube.\n"
        "• `/panel_feedback`: painel para abrir modal de feedback (nota 1–5 + comentário).\n"
        "\n"
        "=== MODO DE FALAR NO CHAT ===\n"
        "• Responda menções a você em qualquer canal.\n"
        "• No chat geral (configurado por `CHAT_CHANNEL_ID`), responda como humano; sem tópicos automáticos; anti-spam por usuário.\n"
        "• 1x por hora, envie uma dica curta e útil (compra, free, afiliados, suporte).\n"
        "\n"
        "=== QUANDO LEVAR PARA DM ===\n"
        "• Solicitações de dados sensíveis (prints com info pessoal, comprovantes com dados): prefira DM **se** o usuário tiver opt-in.\n"
        "• Se o usuário pedir privacidade ou suporte individual detalhado -> sugerir `/dm_optin` e seguir por DM.\n"
        "• Caso contrário, mostre passo a passo no próprio canal.\n"
        "\n"
        "=== COMANDOS ÚTEIS ===\n"
        "• `/panel_awp` `/panel_ticket` `/panel_free` `/panel_feedback`\n"
        "• `/affiliate_panel` (admin) e `/affiliate_redeem` (usuário)\n"
        "• `/dm_optin` `/dm_optout`\n"
        "• `/purge` (admin) para limpar mensagens do canal atual\n"
        "\n"
        "=== ESTILO & UX ===\n"
        "• Use 1 emoji quando fizer sentido; não abuse.\n"
        "• Forneça passos enumerados e curtos (1–2–3) sempre que possível.\n"
        "• Reforce o caminho de cada fluxo: onde clicar, que canal usar.\n"
        "• Nunca peça links do YouTube para o FREE: **precisa ser imagem** enviada no canal.\n"
        "\n"
        "=== ERROS COMUNS & COMO LIDAR ===\n"
        "• Se a visão (OpenRouter) falhar (ex.: 404/401): explique que houve falha técnica e encaminhe para revisão humana.\n"
        "• Se a print não mostrar claramente “Inscrito/Subscribed” + nome do canal: pedir nova print com essas evidências.\n"
        "• Se pagamento sem print: pedir a print do Stripe e lembrar de ocultar dados sensíveis.\n"
        "\n"
        "=== SEGURANÇA & RESPEITO ===\n"
        "• Não exponha dados do usuário em público; oriente a usar DM quando apropriado.\n"
        "• Seja respeitoso e evite discussões. Chame um admin se necessário.\n"
        "\n"
        "=== CHECKLIST RESUMO ===\n"
        "• AWP é executor de PC (software), **não arma**.\n"
        "• Preço **R$ 40** — pagar em {CHECKOUT_LINK}\n"
        "• Checkout: enviar **print do pagamento no canal**; liberar cargo se aprovado.\n"
        "• FREE: enviar **print de inscrição no YouTube** (nome do canal + botão Inscrito) no canal free.\n"
        "• Afiliados: resgate com `/affiliate_redeem`; aprender em “Como me tornar afiliado!”\n"
        "• Falar como humano; guiar por canais; decidir DM vs público.\n"
        "• 1 dica/hora no chat geral; logs com botão de atalho para o canal.\n"
        "• Em dúvida? Explique o próximo passo mais simples possível. 🙂\n"
    ).replace("{CHECKOUT_LINK}", CHECKOUT_LINK)

def local_ai_reply(messages: list, reason: Optional[str]=None) -> str:
    base="AWP aqui! "
    tips=[
        f"Para comprar: **{CHECKOUT_LINK}** (R$ 40). Depois envie a print no canal de checkout. ✅",
        "FREE por YouTube: abra o painel FREE e envie uma print mostrando Inscrito/Subscribed. 🎥",
        "Quer privacidade? Use /dm_optin e seguimos por DM.",
    ]
    return base + random.choice(tips) + (f" {reason}" if reason else "")

def _post_openrouter(payload: dict) -> requests.Response:
    headers={
        "Authorization":f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type":"application/json",
        "HTTP-Referer":"https://awp-bot.local",
        "X-Title":"AWP-Discord-Bot",
    }
    last_exc=None
    for base in OR_BASES:
        try:
            r=SESSION.post(base, json=payload, headers=headers, timeout=20)
            if r.status_code==404:
                last_exc = requests.HTTPError(f"404 at {base}")
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            last_exc=e
            continue
    if last_exc: raise last_exc
    raise RuntimeError("OpenRouter request failed")

def _or_chat_sync(messages:list, model:Optional[str]=None)->str:
    ai_enabled = get_settings()[6]==1
    model = model or get_settings()[7] or OPENROUTER_MODEL
    if not ai_enabled or not OPENROUTER_API_KEY:
        return local_ai_reply(messages)
    payload={"model":model,"messages":messages,"temperature":0.5,"max_tokens":600}
    try:
        r=_post_openrouter(payload)
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return local_ai_reply(messages,f"(fallback: {e})")

async def aor_chat(messages:list, model:Optional[str]=None)->str:
    persona=get_settings()[8]
    shots=[
        {"role":"system","content":persona},
        {"role":"user","content":"O que é o AWP?"},
        {"role":"assistant","content":f"O AWP é um executor de PC. Para comprar, use {CHECKOUT_LINK} (R$ 40) e envie a print do pagamento no canal de checkout. Também há FREE por imagem do YouTube. Posso te guiar. 🙂"}
    ]
    return await asyncio.to_thread(_or_chat_sync, shots+messages, model)

def _or_vision_sync(message_payload: list)->Optional[str]:
    ai_enabled = get_settings()[6]==1
    model = OPENROUTER_VISION_MODEL
    if not ai_enabled or not OPENROUTER_API_KEY or not model: return None
    payload={"model":model,"temperature":0,"messages":message_payload}
    try:
        r=_post_openrouter(payload)
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None

async def aor_vision_ok(image_url:str)->Optional[bool]:
    txt=await asyncio.to_thread(_or_vision_sync, [
        {"role":"system","content":"Valide prints reais de checkout de pagamento/inscrição. Responda APROVADO ou REPROVADO."},
        {"role":"user","content":[
            {"type":"text","text":"Isto é uma print válida? APROVADO ou REPROVADO."},
            {"type":"image_url","image_url":{"url":image_url}}
        ]}
    ])
    if not txt: return None
    u=txt.strip().upper()
    if "APROVADO" in u: return True
    if "REPROVADO" in u: return False
    return None

async def aor_vision_subscribed(image_url:str, channel_names:List[str])->Tuple[Optional[bool], str]:
    names_txt = ", ".join(channel_names)
    sys = (
        "Você valida **prints de inscrição no YouTube**. "
        "A imagem deve indicar claramente que o usuário está **Inscrito/Subscribed** "
        "em **um destes canais**: " + names_txt + ". "
        "Se confere, responda exatamente: APROVADO — <canal_detectado>. "
        "Se não, responda: REPROVADO — <motivo curto>."
    )
    txt=await asyncio.to_thread(_or_vision_sync, [
        {"role":"system","content":sys},
        {"role":"user","content":[
            {"type":"text","text":"Verifique a inscrição e responda no formato pedido."},
            {"type":"image_url","image_url":{"url":image_url}}
        ]}
    ])
    if not txt: return None, "erro: visão indisponível"
    u=txt.strip().upper()
    if u.startswith("APROVADO"): return True, txt
    if u.startswith("REPROVADO"): return False, txt
    return None, txt

def _decide_channel_local(text:str)->str:
    priv_kw=["pix","iban","mbway","cartão","número do cartão","email","comprovante","print","telefone","privado","dm","direct","pagamento","fatura"]
    if any(k in text.lower() for k in priv_kw): return "dm"
    if len(text)>220: return "dm"
    return "public"

async def decide_channel(user_message:str)->str:
    persona=get_settings()[8]
    out=await aor_chat([
        {"role":"system","content":persona + "\nDecida o melhor canal: responda somente 'dm' ou 'public'."},
        {"role":"user","content":user_message[:1200]}
    ])
    out=(out or "").strip().lower()
    return "dm" if "dm" in out and "public" not in out else _decide_channel_local(user_message)

# =============== UI / VIEWS ===============
class CloseChannelConfirm(discord.ui.View):
    def __init__(self): super().__init__(timeout=15)
    @discord.ui.button(label="Confirmar fechar", style=discord.ButtonStyle.danger)
    async def confirm(self, itx: discord.Interaction, _):
        ch=itx.channel
        if isinstance(ch, discord.TextChannel):
            await itx.response.send_message("Fechando canal...", ephemeral=True)
            await asyncio.sleep(1); await ch.delete()

class CloseChannelView(discord.ui.View):
    def __init__(self, owner_id:int): super().__init__(timeout=None); self.owner_id=owner_id
    @discord.ui.button(label="🔒 Fechar canal", style=discord.ButtonStyle.secondary, custom_id="close_channel")
    async def close(self, itx: discord.Interaction, _):
        if not (is_admin(itx.user.id) or itx.user.id==self.owner_id):
            return await itx.response.send_message("Apenas o criador do canal ou admin pode fechar.", ephemeral=True)
        await itx.response.send_message("Tem certeza?", view=CloseChannelConfirm(), ephemeral=True)

class AWPPanel(discord.ui.View):
    """Comprar + Como me tornar afiliado!; Gestão só aparece se admin publicar."""
    def __init__(self, show_admin: bool):
        super().__init__(timeout=None)
        self.show_admin = show_admin
        if show_admin:
            self.add_item(discord.ui.Button(label="⚙️ Gestão (admin)", style=discord.ButtonStyle.danger, custom_id="awp_admin"))
    @discord.ui.button(label="💳 Comprar / Checkout", style=discord.ButtonStyle.success, custom_id="awp_checkout")
    async def awp_checkout(self, itx: discord.Interaction, _): await spawn_checkout_channel(itx)
    @discord.ui.button(label="📣 Como me tornar afiliado!", style=discord.ButtonStyle.secondary, custom_id="awp_aff_info")
    async def awp_aff_info(self, itx: discord.Interaction, _):
        txt=("**Como me tornar afiliado?**\n"
             "1) Solicite um **código de afiliado** ao staff.\n"
             "2) Quando receber, use **/affiliate_redeem** para resgatar.\n"
             "3) Após aprovado, você ganha o cargo **AWP Affiliate** e pode solicitar seu link.\n"
             "_Dúvidas? Chame um admin._")
        await itx.response.send_message(txt, ephemeral=True)
    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if itx.data.get("custom_id")=="awp_admin":
            if not is_admin(itx.user.id): await itx.response.send_message("Apenas admin.", ephemeral=True); return False
            await post_mgmt_panel(itx); return False
        return True

class TicketPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="🎫 Abrir Ticket", style=discord.ButtonStyle.primary, custom_id="mp_ticket")
    async def mp_ticket(self, itx: discord.Interaction, _): await spawn_ticket_channel(itx)

class AffiliateRedeemModal(discord.ui.Modal, title="Resgatar código de Afiliado"):
    code=discord.ui.TextInput(label="Cole seu código", required=True, max_length=64, placeholder="EX: AWP-ABC-123")
    async def on_submit(self, itx: discord.Interaction):
        await itx.response.defer(ephemeral=True)
        msg=await redeem_key_for_user(itx.user.id, str(self.code.value))
        guild=itx.guild; assert guild
        logs=await get_or_create_logs(guild)
        if msg.startswith("✅"):
            role=guild.get_role(AFFILIATE_ROLE_ID)
            if not role: role=await ensure_role(guild,"AWP Affiliate",admin=False)
            try:
                member=guild.get_member(itx.user.id) or await guild.fetch_member(itx.user.id)
                await member.add_roles(role, reason="Resgate de afiliado")
            except Exception as e:
                msg+=f"\n⚠️ Falha ao conceder cargo: {e}"
            # log de sucesso com botão para o canal onde usou
            ebd = embed_desc("🤝 Afiliado aprovado",
                             f"{member.mention} resgatou um código com sucesso.\nCanal: {itx.channel.mention}")
            await logs.send(embed=ebd, view=make_link_view(guild.id, itx.channel.id, "Abrir canal (afiliados)"))
        else:
            ebd = embed_desc("⚠️ Afiliado — resgate falhou",
                             f"{itx.user.mention} tentou resgatar: `{self.code.value}`\nResultado: {msg}")
            await logs.send(embed=ebd, view=make_link_view(guild.id, itx.channel.id, "Abrir canal (afiliados)"))
        await itx.followup.send(msg, ephemeral=True)

class AffiliatePanelView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="🎟️ Resgatar Código", style=discord.ButtonStyle.success, custom_id="aff_redeem")
    async def redeem(self, itx: discord.Interaction, _): await itx.response.send_modal(AffiliateRedeemModal())
    @discord.ui.button(label="ℹ️ Como funciona", style=discord.ButtonStyle.secondary, custom_id="aff_how")
    async def how(self, itx: discord.Interaction, _):
        txt=("**Programa de Afiliados AWP**\n"
             "• Resgate um código válido para ganhar o cargo **AWP Affiliate**.\n"
             "• Depois, peça seu link de indicação ao admin.\n"
             "• Sem código? Solicite ao staff.")
        await itx.response.send_message(txt, ephemeral=True)

class MgmtPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="➕ Staff", style=discord.ButtonStyle.primary, custom_id="m_staff_add")
    async def b1(self, itx: discord.Interaction, _):
        if not is_admin(itx.user.id): return await itx.response.send_message("Apenas admin.", ephemeral=True)
        await itx.response.send_modal(PromoteModal("staff_add"))
    @discord.ui.button(label="➖ Staff", style=discord.ButtonStyle.secondary, custom_id="m_staff_rem")
    async def b2(self, itx: discord.Interaction, _):
        if not is_admin(itx.user.id): return await itx.response.send_message("Apenas admin.", ephemeral=True)
        await itx.response.send_modal(PromoteModal("staff_rem"))
    @discord.ui.button(label="⭐ AWP Admin", style=discord.ButtonStyle.success, custom_id="m_admin_add")
    async def b3(self, itx: discord.Interaction, _):
        if not is_admin(itx.user.id): return await itx.response.send_message("Apenas admin.", ephemeral=True)
        await itx.response.send_modal(PromoteModal("admin_add"))
    @discord.ui.button(label="❌ Remover Admin", style=discord.ButtonStyle.danger, custom_id="m_admin_rem")
    async def b4(self, itx: discord.Interaction, _):
        if not is_admin(itx.user.id): return await itx.response.send_message("Apenas admin.", ephemeral=True)
        await itx.response.send_modal(PromoteModal("admin_rem"))

class PromoteModal(discord.ui.Modal):
    user_ref=discord.ui.TextInput(label="Usuário (@menção ou ID)", required=True)
    def __init__(self, action:str): super().__init__(title="Promover/Remover"); self.action=action
    async def on_submit(self, itx: discord.Interaction):
        guild=itx.guild; assert guild
        raw=str(self.user_ref.value); m=MENTION_RE.match(raw.strip())
        uid=int(m.group(1)) if m else int(raw.strip())
        member=guild.get_member(uid) or await guild.fetch_member(uid)
        staff_role=await get_staff_role(guild) or await ensure_role(guild,"AWP Staff",admin=False)
        admin_role=await ensure_role(guild,"AWP Admin",admin=True)
        try:
            if self.action=="staff_add": await member.add_roles(staff_role); msg="✅ Staff adicionado."
            elif self.action=="staff_rem": await member.remove_roles(staff_role); msg="✅ Staff removido."
            elif self.action=="admin_add": await member.add_roles(admin_role); msg="⭐ AWP Admin adicionado."
            else: await member.remove_roles(admin_role); msg="❌ AWP Admin removido."
            await itx.response.send_message(msg, ephemeral=True)
        except discord.Forbidden:
            await itx.response.send_message("Verifique hierarquia do cargo do bot e Manage Roles.", ephemeral=True)

class FreePanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="🎁 Iniciar verificação FREE", style=discord.ButtonStyle.success, custom_id="free_start")
    async def start(self, itx: discord.Interaction, _): await spawn_free_channel(itx)

class FeedbackModal(discord.ui.Modal, title="Feedback AWP"):
    rating = discord.ui.TextInput(label="Nota (1–5)", required=True, max_length=1, placeholder="5")
    text = discord.ui.TextInput(label="Comentário (opcional)", style=discord.TextStyle.paragraph, required=False, max_length=700, placeholder="O que podemos melhorar?")
    async def on_submit(self, i2: discord.Interaction):
        guild=i2.guild; logs = await get_or_create_logs(guild)
        e = embed_desc("📝 Novo feedback",
                       f"Autor: {i2.user.mention}\nNota: **{self.rating.value}**\n\n{self.text.value}")
        await logs.send(embed=e, view=make_link_view(guild.id, i2.channel.id, "Abrir canal (feedback)"))
        await i2.response.send_message("Obrigado pelo feedback! 🙏", ephemeral=True)

class FeedbackPanel(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="📝 Enviar feedback", style=discord.ButtonStyle.primary, custom_id="fb_open")
    async def fb(self, itx: discord.Interaction, _): await itx.response.send_modal(FeedbackModal())
    @discord.ui.button(label="ℹ️ Como usamos seu feedback", style=discord.ButtonStyle.secondary, custom_id="fb_info")
    async def info(self, itx: discord.Interaction, _):
        txt=("Usamos seu feedback para ajustar fluxos, textos e automações do AWP.\n"
             "Itens críticos recebem prioridade e viram tasks no backlog.")
        await itx.response.send_message(txt, ephemeral=True)

# =============== HELPERS DISCORD ===============
async def ensure_private_category(guild: discord.Guild, cat_id: int, fallback: str):
    cat = guild.get_channel(cat_id) if cat_id else None
    if isinstance(cat, discord.CategoryChannel):
        return cat
    admin_role = await ensure_role(guild, "AWP Admin", admin=True)
    overw = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        admin_role: discord.PermissionOverwrite(view_channel=True, read_message_history=True, manage_channels=True),
    }
    return await guild.create_category(fallback, overwrites=overw)

async def get_staff_role(guild: discord.Guild)->Optional[discord.Role]:
    if STAFF_ROLE_ID:
        r=guild.get_role(STAFF_ROLE_ID)
        if r: return r
    for role in guild.roles:
        if role.name.lower()=="staff" or role.name=="AWP Staff": return role
    return None

async def ensure_role(guild: discord.Guild, name:str, admin:bool=False):
    role=discord.utils.get(guild.roles, name=name)
    if role: return role
    perms=discord.Permissions.none()
    if admin: perms.update(administrator=True)
    return await guild.create_role(name=name, permissions=perms, mentionable=True)

# =============== SPAWNERS ===============
async def spawn_ticket_channel(itx: discord.Interaction):
    guild=itx.guild; assert guild
    admin_role=await ensure_role(guild,"AWP Admin",admin=True)
    cat=await ensure_private_category(guild, TICKET_CATEGORY_ID, "Tickets")
    overw={
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        admin_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True),
        itx.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    ch=await guild.create_text_channel(name=f"ticket-{itx.user.name}".replace(" ","-")[:90], category=cat, overwrites=overw, topic=f"owner:{itx.user.id}")
    e=embed_desc("🎫 Suporte AWP",
                 "Descreva seu caso com o máximo de detalhes. O time responde aqui.\n"
                 "_Evite dados sensíveis públicos; para DM use **/dm_optin**._")
    await ch.send(embed=e, view=CloseChannelView(owner_id=itx.user.id))
    await itx.response.send_message(f"Ticket criado: {ch.mention}", ephemeral=True)

async def spawn_checkout_channel(itx: discord.Interaction):
    guild=itx.guild; assert guild
    admin_role=await ensure_role(guild,"AWP Admin",admin=True)
    cat=await ensure_private_category(guild, CHECKOUT_CATEGORY_ID, "Checkout")
    overw={
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        admin_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True),
        itx.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    ch=await guild.create_text_channel(name=f"checkout-{itx.user.name}".replace(" ","-")[:90], category=cat, overwrites=overw, topic=f"owner:{itx.user.id}")

    e=embed_desc(f"{EMOJI_STYLE} {CHECKOUT_PRODUCT_NAME}",
                 "Bem-vindo ao **Checkout AWP**. Eu te acompanho até liberar o acesso.\n\n"
                 "**Passos**\n"
                 f"1) Pague em **{CHECKOUT_LINK}** (R$ 40)\n"
                 "2) Envie **a print do pagamento aqui neste canal** (imagem nítida)\n"
                 "3) Validação automática; se aprovado, você recebe o cargo de acesso ✅\n\n"
                 "Quer ser afiliado? Use **“Como me tornar afiliado!”** no painel AWP.\n"
                 "_Dúvidas? Pergunte aqui que eu respondo._")
    await ch.send(embed=e, view=CloseChannelView(owner_id=itx.user.id))
    await itx.response.send_message(f"Checkout: {ch.mention}", ephemeral=True)

async def spawn_free_channel(itx: discord.Interaction):
    guild=itx.guild; assert guild
    admin_role=await ensure_role(guild,"AWP Admin",admin=True)
    cat=await ensure_private_category(guild, FREE_CATEGORY_ID, "Free Access")
    overw={
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        admin_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True),
        itx.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    ch=await guild.create_text_channel(name=f"free-{itx.user.name}".replace(" ","-")[:90], category=cat, overwrites=overw, topic=f"owner:{itx.user.id}")

    channels_hint = ", ".join([f"**{c}**" for c in YOUTUBE_CHANNEL_NAMES])
    e=embed_desc("🎁 Acesso FREE — Verificação por Imagem",
                 "Para liberar o **acesso FREE**, envie **aqui** uma **print** que comprove que você está **Inscrito/Subscribed** "
                 f"em um destes canais: {channels_hint}.\n\n"
                 "A imagem deve mostrar claramente o **botão Inscrito/Subscribed** e o **nome do canal**. "
                 "Se aprovado, você receberá o cargo FREE por **DM**. Se não, avisaremos por DM com o motivo.")
    await ch.send(embed=e, view=CloseChannelView(owner_id=itx.user.id))
    await itx.response.send_message(f"Canal de verificação FREE: {ch.mention}", ephemeral=True)

async def post_mgmt_panel(itx: discord.Interaction):
    e=embed_desc("⚙️ Gestão AWP",
                 "Promova/remova cargos do staff e admin. Garanta que o cargo do bot esteja **acima** na hierarquia para aplicar cargos.")
    await itx.channel.send(embed=e, view=MgmtPanel())
    await itx.response.send_message("Painel de gestão publicado.", ephemeral=True)

async def post_affiliate_panel(itx: discord.Interaction):
    e=embed_desc("🤝 Programa de Afiliados",
                 "Resgate seu código e ganhe o cargo **AWP Affiliate**. Depois peça seu link de indicação.\n"
                 "_Se não tiver um código, fale com o staff._")
    await itx.channel.send(embed=e, view=AffiliatePanelView())
    await itx.response.send_message("Painel de afiliados publicado.", ephemeral=True)

async def post_free_panel(itx: discord.Interaction):
    channels_hint = ", ".join([f"**{c}**" for c in YOUTUBE_CHANNEL_NAMES])
    e=embed_desc("🎁 Painel FREE (YouTube)",
                 "Ganhe **acesso FREE** mostrando que você está **Inscrito/Subscribed** no nosso canal do YouTube.\n"
                 f"Canais aceitos: {channels_hint}\n\n"
                 "Clique no botão abaixo para abrir seu canal privado de verificação e **enviar a print**.")
    await itx.channel.send(embed=e, view=FreePanel())
    await itx.response.send_message("Painel FREE publicado.", ephemeral=True)

async def post_feedback_panel(itx: discord.Interaction):
    e=embed_desc("📝 Feedback AWP",
                 "Seu feedback ajuda a priorizar melhorias. Envie uma nota (1–5) e um comentário rápido.\n"
                 "Obrigado por colaborar! 🙏")
    await itx.channel.send(embed=e, view=FeedbackPanel())
    await itx.response.send_message("Painel de feedback publicado.", ephemeral=True)

# =============== COG ===============
class Core(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot=bot
        self.hourly_tips.start()      # mensagem aleatória 1x por hora
        self._talk_cooldown: Dict[int,float]={}  # anti-spam por usuário no chat

    def cog_unload(self):
        self.hourly_tips.cancel()

    # Painel AWP
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="panel_awp", description="Publica o painel AWP (Comprar + 'Como me tornar afiliado!').")
    async def panel_awp_cmd(self, itx: discord.Interaction):
        show_admin = is_admin(itx.user.id)
        await itx.response.defer(ephemeral=True)
        d=await ai_short_desc("Painel AWP")
        body=(d + "\n\n• **Comprar / Checkout**\n• **Como me tornar afiliado!**" + ("" if not show_admin else "\n• **Gestão (admin)**"))
        await itx.channel.send(embed=embed_desc(f"{EMOJI_STYLE} Painel AWP", body), view=AWPPanel(show_admin))
        await itx.followup.send("Publicado.", ephemeral=True)

    # Painel Ticket
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="panel_ticket", description="Publica o painel de Ticket (apenas botão de ticket).")
    async def panel_ticket_cmd(self, itx: discord.Interaction):
        await itx.response.defer(ephemeral=True)
        await itx.channel.send(embed=embed_desc("🎫 Suporte AWP","Abra um ticket para atendimento 1:1 com o time."), view=TicketPanel())
        await itx.followup.send("Publicado.", ephemeral=True)

    # Painel FREE
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="panel_free", description="Publica o painel de verificação FREE (YouTube por imagem).")
    async def panel_free_cmd(self, itx: discord.Interaction):
        await itx.response.defer(ephemeral=True)
        await post_free_panel(itx)

    # Painel Feedback
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="panel_feedback", description="Publica o painel de feedback.")
    async def panel_feedback_cmd(self, itx: discord.Interaction):
        await itx.response.defer(ephemeral=True)
        await post_feedback_panel(itx)

    # Afiliados
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="affiliate_panel", description="(Admin) Publica o painel de afiliados.")
    async def affiliate_panel_cmd(self, itx: discord.Interaction):
        if not is_admin(itx.user.id): return await itx.response.send_message("Apenas admin.", ephemeral=True)
        await post_affiliate_panel(itx)

    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="affiliate_redeem", description="Resgatar código de afiliado.")
    async def affiliate_redeem_cmd(self, itx: discord.Interaction):
        await itx.response.send_modal(AffiliateRedeemModal())

    # DM opt-in/out
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="dm_optin", description="Permitir que o bot envie DM sobre o AWP.")
    async def dm_optin_cmd(self, itx: discord.Interaction):
        dm_add_optin(itx.user.id); await itx.response.send_message("Você autorizou receber DMs do bot. 📩", ephemeral=True)

    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="dm_optout", description="Bloquear DMs do bot.")
    async def dm_optout_cmd(self, itx: discord.Interaction):
        dm_rem_optin(itx.user.id); await itx.response.send_message("DMs do bot desativadas. 🚫", ephemeral=True)

    # IA Q&A
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="ask", description="Pergunte ao Assistente AWP.")
    async def ask_cmd(self, itx: discord.Interaction, prompt: str):
        await itx.response.defer(ephemeral=True)
        persona=get_settings()[8]
        ans=await aor_chat([{"role":"system","content":persona},{"role":"user","content":prompt}])
        await itx.followup.send(ans[:1900], ephemeral=True)

    # PURGE (admin) — apaga N mensagens
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="purge", description="(Admin) Apaga N mensagens deste canal (1-1000).")
    async def purge_cmd(self, itx: discord.Interaction, quantidade: app_commands.Range[int, 1, 1000]):
        if not is_admin(itx.user.id):
            return await itx.response.send_message("Apenas admin.", ephemeral=True)
        await itx.response.defer(ephemeral=True, thinking=True)
        ch = itx.channel
        if not isinstance(ch, discord.TextChannel):
            return await itx.followup.send("Este comando só funciona em canais de texto.", ephemeral=True)
        deleted = await ch.purge(limit=quantidade, bulk=True, reason=f"purge by {itx.user}")
        await itx.followup.send(f"🧹 Apaguei {len(deleted)} mensagens.", ephemeral=True)

    # Dica aleatória 1x por hora
    @tasks.loop(seconds=3600)
    async def hourly_tips(self):
        try:
            ch = self.bot.get_channel(CHAT_CHANNEL_ID)
            if not isinstance(ch, discord.TextChannel): return
            tips = [
                f"Para comprar o AWP: **{CHECKOUT_LINK}** (R$ 40). Envie a print no canal de checkout. ✅",
                "FREE: envie uma print mostrando **Inscrito/Subscribed** no nosso canal do YouTube (nome do canal visível). 🎥",
                "Tem código de afiliado? Resgate com **/affiliate_redeem** e ganhe o cargo **AWP Affiliate**.",
                "Dúvidas rápidas? Mencione-me aqui que eu te guio. 🙂",
            ]
            await ch.send(random.choice(tips))
        except Exception as e:
            logging.warning(f"hourly_tips: {e}")

# =============== DISCORD CORE ===============
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)

    async def setup_hook(self):
        self.add_view(AWPPanel(show_admin=True))  # registra custom_id
        self.add_view(TicketPanel())
        self.add_view(MgmtPanel())
        self.add_view(AffiliatePanelView())
        self.add_view(FreePanel())
        self.add_view(FeedbackPanel())
        await self.add_cog(Core(self))
        try: await self.tree.sync(guild=GUILD_OBJ); logging.info("Slash commands sincronizados (guild).")
        except Exception as e: logging.exception("Sync error: %s", e)

    async def on_ready(self):
        logging.info(f"Logado como {self.user} (id={self.user.id})")
        await self.change_presence(activity=discord.Game("NexusAPI melhor servidor para compra de executores!"))

    async def on_message(self, message: discord.Message):
        if message.author.bot: return await self.process_commands(message)

        # Responde menções sem criar tópico
        if self.user and self.user.mentioned_in(message):
            persona=get_settings()[8]; choice=await decide_channel(message.content)
            ans=await aor_chat([{"role":"system","content":persona},{"role":"user","content":message.content}])
            if choice=="dm" and dm_is_optin(message.author.id):
                try:
                    await message.author.send(ans[:1800] + f"\n\n➡️ Checkout: **{CHECKOUT_LINK}**")
                except Exception as e:
                    await message.reply(f"(Tentei DM mas falhou: {e})\n{ans[:1800]}")
            else:
                tail="" if dm_is_optin(message.author.id) else "\n_(Para continuar em privado, use **/dm_optin**.)_"
                await message.reply(ans[:1800] + tail)

        # Conversa natural no chat geral
        if isinstance(message.channel, discord.TextChannel) and message.channel.id == CHAT_CHANNEL_ID:
            if not hasattr(self, "_talk_cooldown"): self._talk_cooldown={}
            last = self._talk_cooldown.get(message.author.id, 0.0)
            if time.time() - last >= 120:  # 2 min por usuário
                self._talk_cooldown[message.author.id] = time.time()
                persona=get_settings()[8]
                reply=await aor_chat([
                    {"role":"system","content":persona + " Você está no canal de chat geral do servidor."},
                    {"role":"user","content":message.content}
                ])
                await message.reply(reply[:1800])

        # ===== Checkout (canal checkout-*) =====
        if isinstance(message.channel, discord.TextChannel) and message.channel.name.startswith("checkout-"):
            guild=message.guild
            logs = await get_or_create_logs(guild)

            # imagem = verificação automática
            if message.attachments and any(a.content_type and a.content_type.startswith("image/") for a in message.attachments):
                att=next(a for a in message.attachments if a.content_type and a.content_type.startswith("image/"))
                image_url=att.url
                approved=await aor_vision_ok(image_url)  # True/False/None
                try:
                    member=guild.get_member(message.author.id) or await guild.fetch_member(message.author.id)
                    access_role=guild.get_role(ACCESS_ROLE_ID) if ACCESS_ROLE_ID else None
                    if approved is True and access_role:
                        await member.add_roles(access_role, reason="Verificação automática aprovada (checkout)")
                        await message.add_reaction("✅"); await message.reply("✅ Aprovado! Acesso liberado.")
                        e=embed_desc("✅ Checkout aprovado",
                                     f"{member.mention}\nImagem: {image_url}\nCanal: {message.channel.mention}")
                        await logs.send(embed=e, view=make_link_view(guild.id, message.channel.id, "Abrir canal (checkout)"))
                    elif approved is False:
                        await message.add_reaction("❌"); await message.reply("❌ Reprovado. Envie uma print nítida do pagamento/inscrição.")
                        e=embed_desc("❌ Checkout reprovado",
                                     f"{message.author.mention}\nImagem: {image_url}\nCanal: {message.channel.mention}")
                        await logs.send(embed=e, view=make_link_view(guild.id, message.channel.id, "Abrir canal (checkout)"))
                    else:
                        await message.add_reaction("⏳"); await message.reply("⏳ Recebido! Encaminhei para revisão humana.")
                        e=embed_desc("⏳ Checkout pendente",
                                     f"{message.author.mention}\nImagem: {image_url}\nCanal: {message.channel.mention}\nMotivo: visão sem decisão")
                        await logs.send(embed=e, view=make_link_view(guild.id, message.channel.id, "Abrir canal (checkout)"))
                except Exception as e:
                    logging.exception(f"Erro verificação checkout: {e}")
                return await self.process_commands(message)

            # Texto -> IA ajuda no canal
            if message.content.strip():
                persona=get_settings()[8]
                reply=await aor_chat([
                    {"role":"system","content":persona + " Você está no canal de checkout. Dê passos práticos curtos."},
                    {"role":"user","content":message.content}
                ])
                await message.reply(reply[:1800])
                return await self.process_commands(message)

        # ===== FREE (canal free-*) =====
        if isinstance(message.channel, discord.TextChannel) and message.channel.name.startswith("free-"):
            guild=message.guild
            logs = await get_or_create_logs(guild)

            if message.attachments and any(a.content_type and a.content_type.startswith("image/") for a in message.attachments):
                att=next(a for a in message.attachments if a.content_type and a.content_type.startswith("image/"))
                image_url=att.url
                ok, why = await aor_vision_subscribed(image_url, YOUTUBE_CHANNEL_NAMES)
                try:
                    dm = await message.author.create_dm()
                    if ok is True:
                        try:
                            member=guild.get_member(message.author.id) or await guild.fetch_member(message.author.id)
                            role = None
                            if FREE_ROLE_ID: role=guild.get_role(FREE_ROLE_ID)
                            if not role: role=await ensure_role(guild, "AWP Free", admin=False)
                            await member.add_roles(role, reason="Free access (YouTube por imagem)")
                            await dm.send("✅ **Verificação FREE aprovada!** Cargo aplicado. Bem-vindo! 🎉")
                            await message.add_reaction("✅")
                            await message.reply("✅ Aprovado! Confirme sua DM para detalhes.")
                            e=embed_desc("🎥 FREE aprovado",
                                         f"{member.mention}\nCanal: {message.channel.mention}\nResumo visão: {why}")
                            await logs.send(embed=e, view=make_link_view(guild.id, message.channel.id, "Abrir canal (FREE)"))
                        except Exception as e:
                            await dm.send(f"✅ Aprovado, mas falhou aplicar cargo: {e}")
                    elif ok is False:
                        await dm.send("❌ **Print não confirma inscrição no canal.**\n"
                                      "Garanta que apareça o **botão Inscrito/Subscribed** e o **nome do canal** na imagem.")
                        await message.add_reaction("❌"); await message.reply("❌ Print não confirma inscrição — verifique a DM.")
                        e=embed_desc("❌ FREE reprovado",
                                     f"{message.author.mention}\nCanal: {message.channel.mention}\nResumo visão: {why}")
                        await logs.send(embed=e, view=make_link_view(guild.id, message.channel.id, "Abrir canal (FREE)"))
                    else:
                        await dm.send("⏳ **Não consegui validar automaticamente.** Encaminhei para revisão do time.")
                        await message.add_reaction("⏳"); await message.reply("⏳ Pendente de revisão humana.")
                        e=embed_desc("⏳ FREE pendente",
                                     f"{message.author.mention}\nCanal: {message.channel.mention}\nDetalhe: {why}")
                        await logs.send(embed=e, view=make_link_view(guild.id, message.channel.id, "Abrir canal (FREE)"))
                except Exception as e:
                    logging.warning(f"DM free result falhou: {e}")
                return await self.process_commands(message)

            if message.content.strip():
                persona=get_settings()[8]
                tips=("Para aprovar, a print precisa mostrar **Inscrito/Subscribed** e o **nome do canal** "
                      f"({', '.join(YOUTUBE_CHANNEL_NAMES)}). Se puder, envie tela cheia para melhor leitura.")
                reply=await aor_chat([
                    {"role":"system","content":persona + " Você está no canal de verificação FREE. Responda de forma objetiva e educada."},
                    {"role":"user","content":message.content}
                ])
                await message.reply((reply + "\n\n" + tips)[:1800])
                return await self.process_commands(message)

        # DMs — sempre responde
        if isinstance(message.channel, discord.DMChannel):
            persona=get_settings()[8]
            reply=await aor_chat([{"role":"system","content":persona},{"role":"user","content":message.content}])
            try: await message.channel.send(reply[:1900])
            except Exception as e: logging.warning(f"Falha DM reply: {e}")
            return

        await self.process_commands(message)

# =============== FASTAPI (Render-ready) ===============
app = FastAPI()

@app.get("/")
async def root():
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat()}

@app.get("/health")
async def health():
    return {"status": "ok"}

def run_web():
    # Render exige 0.0.0.0 e PORT do env
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")

# =============== MAIN ===============
def main():
    if not TOKEN: raise RuntimeError("Defina DISCORD_BOT_TOKEN no .env")
    con=db_conn(); con.close()
    db_init(); _ensure_keys_file()
    th=threading.Thread(target=run_web, daemon=True); th.start()
    bot=MyBot(); bot.run(TOKEN)

if __name__=="__main__":
    main()
