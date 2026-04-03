from telethon import TelegramClient, events
api_id = 37420141
api_hash = "505f4d9e18b7ecabe2b72ba67757f147"
GROUP_ID = -1003780995114
FORWARD_GROUP = -1003732974023
TARGET_BOTS = [
    "onlyotproxyssbot",
    "bashaotp2bot",
    "hadiotpbot",
    "onlyotproxysmsbot",
    "efbnumberbot",
    "rkmmonitorkmbot",
    "rmkotppbot",
    "basabot3bot",
    "lamiotpbot"
]
client = TelegramClient("session_name", api_id, api_hash)
# 🔥 BOT COPY + PASTE
@client.on(events.NewMessage(chats=GROUP_ID))
async def handler(event):
    try:
        sender = await event.get_sender()
        if not sender:
            return
        username = sender.username.lower() if sender.username else ""
        if username in TARGET_BOTS:
            text = event.raw_text
            if not text:
                return
            print(f"Detected from {username}")
            # ❌ event.out বাদ দাও
            # ❌ skip remove
            # ✅ copy + paste
            await client.send_message(GROUP_ID, text)
    except Exception as e:
        print("ERROR:", e)
# 🔥 FORWARD (আগেরটা ঠিক থাকবে)
@client.on(events.NewMessage(chats=FORWARD_GROUP))
async def forward_handler(event):
    try:
        await event.forward_to(GROUP_ID)
    except Exception as e:
        print("Forward Error:", e)
client.start()
print("🚀 Bot Running...")
client.run_until_disconnected()