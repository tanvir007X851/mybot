from telethon import TelegramClient, events, Button
import re

# 🔑 API info
api_id = 38275762
api_hash = "553c14bc0611908d2bff15c405cce5c6"

# 📥 Source group
source_chat = -1003780995114

# 📤 Target group
target_group = -1003897294907

# 🎯 Target bot
target_bots = ["Facebooknumber1bot"]

client = TelegramClient("session", api_id, api_hash)


# 🔒 Number mask
def mask_number(number):
    if len(number) > 6:
        return number[:-6] + "***" + number[-3:]
    return number


@client.on(events.NewMessage(chats=source_chat))
async def handler(event):
    try:
        sender = await event.get_sender()

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
        masked_number = mask_number(number)

        # 🔍 OTP (FIXED)
        otp_matches = re.findall(r"\b\d{4,8}\b", text)
        if otp_matches:
            otp = otp_matches[-1]   # 🔥 last number = correct OTP
        else:
            otp = "N/A"

        # 🔥 Full SMS (exact same as source)
        sms_match = re.search(r"Full SMS[:\s]*([\s\S]*)", text)
        if sms_match:
            full_sms = sms_match.group(1).strip()
        else:
            full_sms = text.strip()

        # 🎨 Final Message
        new_msg = f"""✅ {flag} 𝑭𝒂𝒄𝒆𝒃𝒐𝒐𝒌 𝑶𝑻𝑷 𝑪𝒐𝒅𝒆 𝑹𝒆𝒄𝒆𝒊𝒗𝒆𝒅 𝑺𝒖𝒄𝒄𝒆𝒔𝒔𝒇𝒖𝒍𝒍𝒚 ✨
📱 𝑵𝒖𝒎𝒃𝒆𝒓: {masked_number}
🏆 𝗥𝗲𝘄𝗮𝗿𝗱: 𝟬.𝟬𝟬𝟭$
🔑 𝑶𝑻𝑷 𝑪𝒐𝒅𝒆 : {otp}

𝑵𝒖𝒎𝒃𝒆𝒓 𝑩𝒐𝒕 👇: 
@Facebooknumber1bot

💬 Full SMS:
{full_sms}
"""

        # 🔘 Buttons
        buttons = [
            [Button.url("📢 Join Channel", "https://t.me/your_channel")],
            [Button.url("📲 Number Bot", "https://t.me/your_bot")]
        ]

        # 📤 Send (quote style সহ)
        await client.send_message(
            target_group,
            new_msg,
            buttons=buttons,
            reply_to=event.message.id
        )

    except Exception as e:
        print("❌ Error:", e)


client.start()
print("✅ Bot Running Successfully...")
client.run_until_disconnected()