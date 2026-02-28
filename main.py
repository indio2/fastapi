import os
import re
from typing import Dict

import httpx
from fastapi import FastAPI, Request

from openai import OpenAI

app = FastAPI()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Minimalne "stany" (na MVP). Później przeniesiesz to do bazy.
USER_PERSONA: Dict[int, str] = {}
USER_MODE: Dict[int, str] = {}   # "actor" | "coach" | "analyze"
FREE_USES: Dict[int, int] = {}   # start 5

client = OpenAI(api_key=OPENAI_API_KEY)

PERSONAS = {
    "sato": "Sato — Cold Professional (female). Calm, rational, emotionally distant. Short messages.",
    "maja": "Maja — Fearful-Avoidant (female). Warm then withdraw, guarded, push-pull.",
    "oscar": "Oscar — Defensive/Narcissistic traits (male). Charming, deflecting, avoids accountability."
}

ACTOR_INSTRUCTIONS = """
You are BrainCora ACTOR — a simulation engine for training difficult conversations.
Roleplay as the selected persona. Output ONLY one chat message as that persona.
Safety: no threats, insults, coercion, stalking, self-harm content.
Keep concise (max ~120 words).
Use the user's language if possible.
"""

COACH_INSTRUCTIONS = """
You are BrainCora COACH/EVALUATOR.
Score 0–2 each: Validation, Boundary, Specificity, Calmness, No-Spiral-Feeding.
Then: 2 bullets good, 2 bullets improve, 1 better reply (max 3 sentences), 1 micro-drill.
Be practical, not therapeutic. Use user's language.
"""

ANALYZER_INSTRUCTIONS = """
You are BrainCora ANALYZER.
User pastes a conversation. Explain what's happening (bullets), likely pattern (tentative),
red flags (if any), what to do now, and 2–3 reply options.
No diagnosing. Use user's language.
"""

async def tg_send(chat_id: int, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=20) as h:
        await h.post(url, json={"chat_id": chat_id, "text": text})

def ensure_user(user_id: int):
    if user_id not in FREE_USES:
        FREE_USES[user_id] = 5
    if user_id not in USER_MODE:
        USER_MODE[user_id] = "actor"
    if user_id not in USER_PERSONA:
        USER_PERSONA[user_id] = "maja"  # domyślnie

def take_free_use(user_id: int) -> bool:
    """Zwraca True jeśli wolno użyć (free albo pro w przyszłości)."""
    ensure_user(user_id)
    if FREE_USES[user_id] > 0:
        FREE_USES[user_id] -= 1
        return True
    return False

def pick_persona_from_start(text: str):
    # Telegram deep link daje: "/start maja" lub "/start maja_something"
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 2:
        payload = parts[1].lower()
        if payload.startswith("maja"):
            return "maja"
        if payload.startswith("sato"):
            return "sato"
        if payload.startswith("oscar"):
            return "oscar"
    return None

def openai_reply(instructions: str, user_text: str, persona_hint: str = "") -> str:
    prompt = user_text
    if persona_hint:
        prompt = f"Persona: {persona_hint}\n\nUser:\n{user_text}"

    resp = client.responses.create(
        model="gpt-4.1-mini",
        instructions=instructions,
        input=prompt
    )
    return resp.output_text.strip() or "…"

@app.get("/")
async def root():
    return {"ok": True, "service": "BrainCora FastAPI"}

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "Missing TELEGRAM_BOT_TOKEN"}

    data = await request.json()

    msg = data.get("message") or data.get("edited_message")
    if not msg:
        return {"ok": True}

    chat_id = msg["chat"]["id"]
    user_id = msg["from"]["id"]
    text = (msg.get("text") or "").strip()

    ensure_user(user_id)

    # Komendy
    if text.startswith("/start"):
        p = pick_persona_from_start(text)
        if p:
            USER_PERSONA[user_id] = p
        USER_MODE[user_id] = "actor"
        await tg_send(chat_id,
            f"BrainCora ✅\nWybrana persona: {USER_PERSONA[user_id]}\n"
            f"Tryb: ACTOR\n\nKomendy:\n/actor\n/coach\n/analyze\n/persona"
        )
        return {"ok": True}

    if text.startswith("/persona"):
        await tg_send(chat_id, "Wybierz personę:\n- sato\n- maja\n- oscar\nNapisz np: persona: oscar")
        return {"ok": True}

    if text.lower().startswith("persona:"):
        val = text.split(":", 1)[1].strip().lower()
        if val in PERSONAS:
            USER_PERSONA[user_id] = val
            await tg_send(chat_id, f"OK. Persona ustawiona na: {val}")
        else:
            await tg_send(chat_id, "Nie znam tej persony. Dostępne: sato, maja, oscar")
        return {"ok": True}

    if text.startswith("/actor"):
        USER_MODE[user_id] = "actor"
        await tg_send(chat_id, "Tryb: ACTOR ✅ Napisz wiadomość, a ja odpowiem jako wybrana persona.")
        return {"ok": True}

    if text.startswith("/coach"):
        USER_MODE[user_id] = "coach"
        await tg_send(chat_id, "Tryb: COACH ✅ Wklej swoją propozycję odpowiedzi, a dam scoring i lepszą wersję.")
        return {"ok": True}

    if text.startswith("/analyze"):
        USER_MODE[user_id] = "analyze"
        await tg_send(chat_id, "Tryb: ANALYZE ✅ Wklej rozmowę (kilka wiadomości), a ja ją przeanalizuję.")
        return {"ok": True}

    # Paywall (na MVP: 5 free użyć)
    if not take_free_use(user_id):
        await tg_send(chat_id, "Limit darmowych użyć wykorzystany. (Tu później wstawisz link do płatności).")
        return {"ok": True}

    mode = USER_MODE[user_id]
    persona = USER_PERSONA[user_id]
    persona_hint = PERSONAS.get(persona, "")

    if mode == "actor":
        out = openai_reply(ACTOR_INSTRUCTIONS, text, persona_hint=persona_hint)
    elif mode == "coach":
        out = openai_reply(COACH_INSTRUCTIONS, text)
    else:
        out = openai_reply(ANALYZER_INSTRUCTIONS, text)

    await tg_send(chat_id, out)
    return {"ok": True}
