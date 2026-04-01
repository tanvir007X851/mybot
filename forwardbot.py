from telethon import TelegramClient, events

# 🔑 API info
api_id = 38275762
api_hash = "553c14bc0611908d2bff15c405cce5c6"

# 📌 Main Group (যেখানে সব আসবে)
GROUP_ID = -1003780995114

# 📌 Extra Source Group (নতুন)
FORWARD_GROUP = -1003732974023

# ✅ একাধিক bot
TARGET_BOTS = [
    "Onlyotproxyssbot",
    "bashaotp2bot",
    "Hadiotpbot",
    "Onlyotproxysmsbot",
    "EFBnumberbot",
    "rkmmonitorkmbot",
    "Rmkotppbot",
    "Basabot3bot",
    "Lamixotpbot"
]

# 🚀 Client start
client = TelegramClient("session_name", api_id, api_hash)


# =========================
# 🔥 1. BOT MESSAGE DETECT (আগেরটা same)
# =========================
@client.on(events.NewMessage(chats=GROUP_ID))
async def handler(event):
    try:
        sender = await event.get_sender()

        if not sender:
            return

        username = sender.username.lower() if sender.username else ""

        if username in [bot.lower() for bot in TARGET_BOTS]:

            text = event.raw_text

            if not text:
                return

            print(f"\n📥 Detected from {username}:")
            print(text)

            if event.out:
                return

            await client.send_message(GROUP_ID, text)

    except Exception as e:
        print("❌ ERROR:", e)


# =========================
# 🔥 2. EXTRA GROUP → MAIN GROUP FORWARD
# =========================
@client.on(events.NewMessage(chats=FORWARD_GROUP))
async def forward_handler(event):
    try:
        if event.out:
            return

        print("\n📤 Forwarding from extra group...")

        # 👉 direct forward (copy না, original forward)
        await client.forward_messages(
            GROUP_ID,
            event.message
        )

    except Exception as e:
        print("❌ FORWARD ERROR:", e)


# ▶️ Run
async def main():
    print("🚀 Userbot is running...")
    await client.run_until_disconnected()


client.start()
client.loop.run_until_complete(main())