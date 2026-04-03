from telethon import TelegramClient, events, Button
import re

# 🔑 তোমার API info বসাও
api_id = 38275762
api_hash = "553c14bc0611908d2bff15c405cce5c6"

# 📥 Source group
source_chat = -1003780995114

# 📤 Target group
target_group = -1003897294907

# 🎯 Target bots
target_bots = ["Facebooknumber1bot"]

client = TelegramClient("session", api_id, api_hash)


# 🔒 Number mask (+51925***261)
def mask_number(number):
    if len(number) > 6:
        return number[:-6] + "***" + number[-3:]
    return number


@client.on(events.NewMessage(chats=source_chat))
async def handler(event):
    try:
        sender = await event.get_sender()

        # 🎯 Bot filter
        if not sender or sender.username not in target_bots:
            return

        text = event.raw_text
        if not text:
            return

        # 🔍 Flag
        flag_match = re.search(r"[\U0001F1E6-\U0001F1FF]{2}", text)
        flag = flag_match.group() if flag_match else "🌍"

        # 🔍 Number
        number_match = re.search(r"\+\d+", text)
        number = number_match.group() if number_match else "N/A"

        # 🔒 Mask
        masked_number = mask_number(number)

        # 🔍 OTP
        otp_match = re.search(r"\b\d{4,8}\b", text)
        otp = otp_match.group() if otp_match else "N/A"

        # 🎨 Final Message (SMS hidden)
        new_msg = f"""✅ {flag} Facebook OTP Code Received Successfully 🎉🔥🥵

📱 Number: {masked_number}
🔑 OTP Code: {otp}

𝐍𝐔𝐌𝐁𝐄𝐑 𝐁𝐎𝐓 🔥 -- 
@Facebooknumber1bot

💬 Full SMS:
••••••••••
"""

        # 🔘 Buttons
        buttons = [
            [Button.url("📢 Join Channel", "https://t.me/your_channel")],
            [Button.url("📲 Number Bot", "https://t.me/your_bot")]
        ]

        await client.send_message(target_group, new_msg, buttons=buttons)

    except Exception as e:
        print("❌ Error:", e)


client.start()
print("✅ Bot Running Successfully...")
client.run_until_disconnected()