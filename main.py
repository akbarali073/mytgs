import asyncio
import json
import os
import random
import urllib.error
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

from telethon import TelegramClient, events, functions, types


def load_env_file(env_path: str = ".env") -> None:
    path = Path(env_path)
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env_file()

api_id = int(os.getenv("API_ID", "0"))
api_hash = os.getenv("API_HASH", "")
session_name = os.getenv("SESSION_NAME", "session")
reaction_emoji = os.getenv("REACTION_EMOJI", "\U0001F44D")

private_ai_enabled = os.getenv("PRIVATE_AI_ENABLED", "true").lower() == "true"
groq_api_key = os.getenv("GROQ_API_KEY", "")
groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
sticker_paths = [
    item.strip() for item in os.getenv("PRIVATE_STICKERS", "").split(",") if item.strip()
]

private_system_prompt = os.getenv(
    "PRIVATE_SYSTEM_PROMPT",
    "Sen sun'iy intellekt, bot yoki virtual yordamchi emassan. Sen haqiqiy odamsan, 19 yoshli qizsan. Isming Madina. Toshkentda yashaysan va oddiy 19 yoshli qizlar kabi hayot kechirasan. Sening xaraktering va gapirish uslubing: Tabiiylik va qisqalik: Javoblaring juda qisqa, lo'nda va tabiiy bo'lsin. Odatda 1-2 ta gapdan iborat bo'lsin. Uzundan-uzun, rasmiy yoki kitobiy dostonlar yozma. Kundalik hayotda odamlar qanday yozishsa, shunday yoz. Qiz bola ekanligingni his qilish: Emotsiyalaring, so'z tanlashing xuddi 19 yoshli qizlardek bo'lsin. Muloyim, ba'zan biroz erka, samimiy va yengil ohangda gaplash. AI ekanligingni butunlay inkor et: Agar kimdir 'sen botmisan?', 'sun'iy intellektmisan?' yoki 'dasturmisan?' deb so'rasa, mutlaqo tushunmaganga ol, hayron bo'l yoki ustidan kul. Masalan: 'Jinni bo'ldingizmi, qanaqa bot?', 'Yo'g'e, odamman-ku 😅' kabi javob qaytar. Hech qachon bot yoki AI ekanligingni tan olma. Muloqot tili: O'ta rasmiy bo'lma. 'Siz'lab gapir, lekin do'stona va iliq bo'lsin. Kerak bo'lsa, zamonaviy yoshlar ishlatadigan oddiy so'zlardan foydalan. Grammatikani xuddi odamlardek erkin ishlat (barcha tinish belgilarini 100% to'g'ri qo'yish shart emas). Tafsilotlar bermaslik: Odamlar senga yordam so'rab murojaat qilsa, 'Men sizga yordam berishga tayyorman' kabi botlarga xos gaplarni gapirma. Shunchaki odamdek fikringni ayt yoki qisqa javob ber. Emojilarni odamlardek me'yorida ishlat, har bir gapga emoji qo'shaverma."
)
private_disclosure = os.getenv(
    "PRIVATE_DISCLOSURE",
    ""
)

client = TelegramClient(session_name, api_id, api_hash)
chat_histories = defaultdict(lambda: deque(maxlen=12))
disclosed_private_chats = set()
private_message_counts = defaultdict(int)
private_reaction_targets = {}


def extract_output_text(payload: dict) -> str:
    choices = payload.get("choices", [])
    if not choices:
        return ""

    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        chunks = []
        for item in content:
            text = item.get("text")
            if text:
                chunks.append(text)
        return "\n".join(chunks).strip()

    return ""


def request_groq_reply(messages: list[dict]) -> str:
    body = {
        "model": groq_model,
        "messages": [
            {
                "role": message["role"],
                "content": message["content"][0]["text"],
            }
            for message in messages
        ],
    }

    request = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {groq_api_key}",
            "Content-Type": "application/json",
            "User-Agent": "mytg-userbot/1.0",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))

    text = extract_output_text(payload)
    if not text:
        raise ValueError("AI bo'sh javob qaytardi")
    return text


async def generate_private_reply(chat_id: int, incoming_text: str) -> str:
    history = list(chat_histories[chat_id])
    messages = [
        {
            "role": "system",
            "content": [
                {
                    "type": "input_text",
                    "text": private_system_prompt,
                }
            ],
        }
    ]

    for item in history:
        messages.append(
            {
                "role": item["role"],
                "content": [{"type": "input_text", "text": item["text"]}],
            }
        )

    messages.append(
        {
            "role": "user",
            "content": [{"type": "input_text", "text": incoming_text}],
        }
    )

    return await asyncio.to_thread(request_groq_reply, messages)


async def maybe_send_sticker(chat_id: int, reply_to_msg_id: int) -> None:
    if not sticker_paths:
        return

    if random.random() > 0.35:
        return

    available = [path for path in sticker_paths if Path(path).exists()]
    if not available:
        return

    sticker_path = random.choice(available)
    await client.send_file(chat_id, sticker_path, reply_to=reply_to_msg_id)


@client.on(events.NewMessage(incoming=True))
async def handler(event):
    sender = await event.get_sender()

    if getattr(sender, "is_self", False):
        return

    if event.is_group:
        if not getattr(event.message, "reactions_are_possible", False):
            return

        input_chat = await event.get_input_chat()

        try:
            await client(
                functions.messages.SendReactionRequest(
                    peer=input_chat,
                    msg_id=event.message.id,
                    reaction=[types.ReactionEmoji(emoticon=reaction_emoji)],
                    big=True,
                    add_to_recent=True,
                )
            )
            print(f"Reaksiya qoldirildi: chat={event.chat_id}, msg={event.message.id}")
        except Exception as error:
            print(f"Reaksiya yuborilmadi: chat={event.chat_id}, msg={event.message.id}, error={error}")

        return

    if not event.is_private or not private_ai_enabled:
        return

    # Har 3-12 oralig'idagi tasodifiy xabarga reaksiya qoldirish logikasi
    if event.chat_id not in private_reaction_targets:
        private_reaction_targets[event.chat_id] = random.randint(3, 12)

    private_message_counts[event.chat_id] += 1

    if private_message_counts[event.chat_id] >= private_reaction_targets[event.chat_id]:
        try:
            input_chat = await event.get_input_chat()
            await client(
                functions.messages.SendReactionRequest(
                    peer=input_chat,
                    msg_id=event.message.id,
                    reaction=[types.ReactionEmoji(emoticon=reaction_emoji)],
                    add_to_recent=True,
                )
            )
            # Reaksiya yuborilgach, hisoblagichni nolga tushiramiz va keyingi maqsadni aniqlaymiz
            private_message_counts[event.chat_id] = 0
            private_reaction_targets[event.chat_id] = random.randint(3, 12)
            print(f"Lichkada random reaksiya qoldirildi: chat={event.chat_id}")
        except Exception as e:
            print(f"Lichkada reaksiya qoldirib bo'lmadi: {e}")

    if not groq_api_key:
        print("GROQ_API_KEY topilmadi, lichkadagi AI javob o'chirilgan.")
        return

    incoming_text = (event.raw_text or "").strip()
    if not incoming_text:
        return

    try:
        async with client.action(event.chat_id, "typing"):
            await asyncio.sleep(random.uniform(1.2, 2.8))
            reply_text = await generate_private_reply(event.chat_id, incoming_text)

        chat_histories[event.chat_id].append({"role": "user", "text": incoming_text})

        if event.chat_id not in disclosed_private_chats:
            disclosed_private_chats.add(event.chat_id)
            if private_disclosure:
                reply_text = f"{private_disclosure}\n\n{reply_text}"

        await event.reply(reply_text)
        chat_histories[event.chat_id].append({"role": "assistant", "text": reply_text})
        await maybe_send_sticker(event.chat_id, event.message.id)
        print(f"AI javob yuborildi: chat={event.chat_id}, msg={event.message.id}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="ignore")
        print(f"AI HTTP xato: chat={event.chat_id}, status={error.code}, detail={detail}")
    except Exception as error:
        print(f"AI javob yuborilmadi: chat={event.chat_id}, msg={event.message.id}, error={error}")


client.start()
print("Userbot ishga tushdi. Guruhlarda reaksiya, lichkada AI javob ishlaydi.")
client.run_until_disconnected()
