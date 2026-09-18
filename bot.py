import json
import os
import time
import uuid
import logging
logging.basicConfig(level=logging.INFO)

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# --- Credentials ---
api_id = 32051210
api_hash = "84961065b7130bff6c318d560184a771"
bot_token = "8624496853:AAEtn8RfgW_tsBlTb9a31UxGinNg_TvPso8"
channel_id = 1726626416

# Telegram user IDs that can approve requests, manage media, and broadcast.
allowed_users = [1726626416, 8186517672, 8695702315, 8805959480, 8835114261, 8725649951, 8728602793, 8877459393, 8333162009, 8242341040, 8839242725]


def load_json(filename):
    if not os.path.exists(filename):
        return {}
    try: 
        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)


app = Client("media_getter_mbot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)
links_db = load_json("links.json")
users_db = load_json("users.json")
user_states = {}


def is_admin(user_id):
    return user_id in allowed_users


def is_authorized(user_id):
    """Whether the user has access to at least one media link."""
    return bool(users_db.get(str(user_id), {}).get("authorized_links", []))


def is_authorized_for_link(user_id, link_id):
    """Authorization is stored per link, not once for every media item."""
    return link_id in users_db.get(str(user_id), {}).get("authorized_links", [])


def save_users():
    save_json("users.json", users_db)


def save_user_info(user):
    old_info = users_db.get(str(user.id), {})
    users_db[str(user.id)] = {
        "name": user.first_name or "Unknown",
        "username": user.username or "N/A",
        "authorized": False,
        "authorized_links": old_info.get("authorized_links", []),
        "pending_links": old_info.get("pending_links", []),
        # IDs of media messages sent by this bot, used for later revocation.
        "delivered_media": old_info.get("delivered_media", []),
        "delivered_media_by_link": old_info.get("delivered_media_by_link", {})
    }
    save_users()


def send_media(client, chat_id, link_info, user_id=None, link_id=None):
    """Copy all media saved for a link to a user."""
    delivered_ids = []
    for message_id in link_info["media"]:
        try:
            copied = client.copy_message(chat_id, channel_id, message_id)
            delivered_ids.append(copied.id)
            time.sleep(0.05)
        except Exception as error:
            print(f"Media delivery failed for {message_id}: {error}")

    # Save message IDs only for customer deliveries, not internal/admin copies.
    if user_id is not None and delivered_ids:
        user_info = users_db.setdefault(str(user_id), {})
        if link_id:
            user_info.setdefault("delivered_media_by_link", {}).setdefault(link_id, []).extend(delivered_ids)
        save_users()
    return delivered_ids


def find_saved_user(identifier):
    """Find a known user by numeric ID or a username previously saved by /start."""
    value = identifier.strip()
    if value.isdigit():
        return value if value in users_db else None

    username = value.lstrip("@").casefold()
    for saved_id, user_info in users_db.items():
        if str(user_info.get("username", "")).lstrip("@").casefold() == username:
            return saved_id
    return None


def find_link(identifier):
    """Find a saved link by its code, full deep link, or exact label."""
    value = identifier.strip()
    if "start=" in value:
        value = value.split("start=", 1)[1].split("&", 1)[0].strip()
    if value in links_db:
        return value

    matching_links = [
        link_id for link_id, info in links_db.items()
        if str(info.get("label", "")).casefold() == value.casefold()
    ]
    return matching_links[0] if len(matching_links) == 1 else None


def make_user_list_chunks():
    """Build Telegram-safe user-list message bodies without cutting entries."""
    entries = []
    for saved_id, user_info in sorted(
        users_db.items(),
        key=lambda item: str(item[1].get("username", "")).casefold()
    ):
        username = user_info.get("username") or "No username"
        if username != "N/A" and not username.startswith("@"):
            username = f"@{username}"

        labels = []
        seen_links = set()
        for link_id in user_info.get("authorized_links", []):
            if link_id in seen_links:
                continue
            seen_links.add(link_id)
            link_info = links_db.get(link_id)
            labels.append(
                str(link_info.get("label") or link_id)
                if link_info else f"Deleted link ({link_id})"
            )

        access_text = ", ".join(labels) if labels else "No approved links"
        entries.append(f"{username} - {saved_id} - {access_text}")

    if not entries:
        return ["No users have started the bot yet."]

    # Telegram allows 4,096 characters. Leave space for the part header.
    maximum_body_length = 3_600
    chunks = []
    current_chunk = ""
    for entry in entries:
        if len(entry) > maximum_body_length:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            chunks.extend(
                entry[index:index + maximum_body_length]
                for index in range(0, len(entry), maximum_body_length)
            )
        elif not current_chunk:
            current_chunk = entry
        elif len(current_chunk) + len(entry) + 1 <= maximum_body_length:
            current_chunk += f"\n{entry}"
        else:
            chunks.append(current_chunk)
            current_chunk = entry
    if current_chunk:
        chunks.append(current_chunk)
    return chunks


def revoke_delivered_media(client, user_id, message_ids):
    """Delete recorded bot media from a private chat in safe batches."""
    deleted = 0
    for start_index in range(0, len(message_ids), 100):
        batch = message_ids[start_index:start_index + 100]
        try:
            client.delete_messages(int(user_id), batch, revoke=True)
            deleted += len(batch)
        except Exception as error:
            print(f"Could not delete media for {user_id}: {error}")
    return deleted


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Save Your Media", callback_data="save_media")],
        [InlineKeyboardButton("All Codes", callback_data="all_codes")],
        [InlineKeyboardButton("Delete Link", callback_data="delete_link")],
        [InlineKeyboardButton("All Users", callback_data="all_users")],
        [InlineKeyboardButton("Revoke Access", callback_data="revoke_access")],
        [InlineKeyboardButton("Broadcast", callback_data="broadcast")]
    ])


@app.on_message(filters.command("start"))
def start(client, message):
    if not message.from_user:
        return

    user_id = message.from_user.id
    save_user_info(message.from_user)
    args = message.text.split(maxsplit=1)

    if len(args) > 1:
        link_id = args[1].strip()
        link_info = links_db.get(link_id)
        if not link_info:
            message.reply("Invalid link.")
            return

        if is_authorized_for_link(user_id, link_id):
            message.reply("Fetching your media...")
            send_media(client, message.chat.id, link_info, user_id, link_id)
            message.reply(f"Label: {link_info.get('label', 'No Label')}")
            return

        # Avoid duplicate notifications for the same link, while allowing
        # the user to request a different link at the same time.
        user_info = users_db[str(user_id)]
        pending_links = user_info.setdefault("pending_links", [])
        if link_id in pending_links:
            message.reply("Your authorization request is already pending approval.")
            return

        pending_links.append(link_id)
        save_users()
        username = f"@{message.from_user.username}" if message.from_user.username else "No username"
        request_text = (
            "New media authorization request\n\n"
            f"Name: {message.from_user.first_name or 'Unknown'}\n"
            f"Username: {username}\n"
            f"User ID: {user_id}\n"
            f"Label: {link_info.get('label', 'No Label')}"
        )
        approval_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Accept", callback_data=f"auth:accept:{user_id}:{link_id}"),
            InlineKeyboardButton("Reject", callback_data=f"auth:reject:{user_id}:{link_id}")
        ]])
        for admin_id in allowed_users:
            try:
                client.send_message(admin_id, request_text, reply_markup=approval_keyboard)
            except Exception as error:
                print(f"Could not notify admin {admin_id}: {error}")
        message.reply("Your request has been sent to the admin for approval.")
        return

    if is_admin(user_id):
        message.reply("Welcome! Choose an option:", reply_markup=admin_keyboard())
    else:
        message.reply("Open the media link you received to request access.")


@app.on_callback_query()
def buttons(client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data

    if data.startswith("auth:"):
        if not is_admin(user_id):
            callback_query.answer("Not allowed.", show_alert=True)
            return

        parts = data.split(":", 3)
        if len(parts) != 4:
            callback_query.answer("This request has expired. Ask the user to open the link again.", show_alert=True)
            return
        _, decision, requested_id, link_id = parts
        requested_user = users_db.get(requested_id)
        if not requested_user or link_id not in requested_user.get("pending_links", []):
            callback_query.answer("This request was already handled.", show_alert=True)
            return

        requested_user["pending_links"].remove(link_id)
        if decision == "accept":
            requested_user.setdefault("authorized_links", []).append(link_id)
            requested_user["authorized"] = True
            save_users()
            callback_query.message.edit_text("Authorization accepted.")
            try:
                client.send_message(int(requested_id), "Your request was approved. Sending your media...")
                send_media(client, int(requested_id), links_db[link_id], int(requested_id), link_id)
                client.send_message(int(requested_id), f"Label: {links_db[link_id].get('label', 'No Label')}")
            except Exception as error:
                print(f"Could not deliver approved media to {requested_id}: {error}")
        else:
            save_users()
            callback_query.message.edit_text("Authorization rejected.")
            try:
                client.send_message(int(requested_id), "Your media authorization request was rejected.")
            except Exception as error:
                print(f"Could not notify rejected user {requested_id}: {error}")
        callback_query.answer()
        return

    if not is_admin(user_id):
        callback_query.answer("Not allowed.", show_alert=True)
        return

    if data == "save_media":
        user_states[user_id] = {"mode": "saving", "media": []}
        callback_query.message.reply("Send media. Type /done when finished.")
    elif data == "all_codes":
        bot_username = client.get_me().username
        codes = [
            f"{info.get('label', 'No Label')} -> https://t.me/{bot_username}?start={link_id}"
            for link_id, info in links_db.items()
        ]
        callback_query.message.reply("\n".join(codes) or "No links.")
    elif data == "delete_link":
        user_states[user_id] = {"mode": "deleting"}
        callback_query.message.reply("Send the link ID to delete.")
    elif data == "all_users":
        user_list_chunks = make_user_list_chunks()
        total_parts = len(user_list_chunks)
        for part_number, chunk in enumerate(user_list_chunks, start=1):
            client.send_message(
                callback_query.message.chat.id,
                f"All Users ({len(users_db)}) — Part {part_number}/{total_parts}\n\n{chunk}"
            )
            # Keep multi-part admin messages within Telegram's per-chat limit.
            if part_number < total_parts:
                time.sleep(1.05)
    elif data == "revoke_access":
        revoke_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Complete Revoke", callback_data="revoke_complete"),
            InlineKeyboardButton("Single Revoke", callback_data="revoke_single")
        ]])
        callback_query.message.reply("Choose the access you want to revoke:", reply_markup=revoke_keyboard)
    elif data == "revoke_complete":
        user_states[user_id] = {"mode": "revoke_complete_user"}
        callback_query.message.reply("Send the user's Telegram ID or @username for complete revocation.")
    elif data == "revoke_single":
        user_states[user_id] = {"mode": "revoke_single_user"}
        callback_query.message.reply("Send the user's Telegram ID or @username for single-link revocation.")
    elif data == "broadcast":
        user_states[user_id] = {"mode": "broadcast"}
        callback_query.message.reply("Send one text or media message to broadcast to all users.")
    callback_query.answer()


@app.on_message(filters.media)
def media_handler(client, message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    state = user_states.get(user_id, {})

    if state.get("mode") == "saving":
        state["media"].append(message.id)
    elif state.get("mode") == "broadcast":
        delivered = 0
        for recipient_id in list(users_db):
            try:
                client.copy_message(int(recipient_id), message.chat.id, message.id)
                delivered += 1
                time.sleep(0.05)
            except Exception as error:
                print(f"Broadcast failed for {recipient_id}: {error}")
        message.reply(f"Broadcast sent to {delivered} users.")
        del user_states[user_id]
@app.on_message(filters.command("done"))
def done(client, message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    state = user_states.get(message.from_user.id)
    if not state or state.get("mode") != "saving" or not state["media"]:
        message.reply("No media is being saved.")
        return
    state["mode"] = "label"
    message.reply("Send the label for this media link.")


@app.on_message(filters.text & ~filters.command(["start", "done"]))
def text_handler(client, message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    state = user_states.get(user_id)
    if not state or not is_admin(user_id):
        return

    if state.get("mode") == "label":
        link_id = uuid.uuid4().hex[:8]
        saved_ids = []
        message.reply("Saving media...")
        for source_message_id in state["media"]:
            try:
                copied = client.copy_message(channel_id, message.chat.id, source_message_id)
                saved_ids.append(copied.id)
                time.sleep(0.05)
            except Exception as error:
                print(f"Could not save media: {error}")
        if not saved_ids:
            message.reply("No valid media was saved.")
            return
        links_db[link_id] = {"media": saved_ids, "label": message.text}
        save_json("links.json", links_db)
        bot_username = client.get_me().username
        message.reply(f"Saved!\nhttps://t.me/{bot_username}?start={link_id}")
        del user_states[user_id]
    elif state.get("mode") == "deleting":
        link_id = message.text.strip()
        if link_id not in links_db:
            message.reply("Invalid link ID.")
            return
        del links_db[link_id]
        save_json("links.json", links_db)
        message.reply("Link deleted.")
        del user_states[user_id]
    elif state.get("mode") == "broadcast":
        delivered = 0
        for recipient_id in list(users_db):
            try:
                client.send_message(int(recipient_id), message.text)
                delivered += 1
                time.sleep(0.05)
            except Exception as error:
                print(f"Broadcast failed for {recipient_id}: {error}")
        message.reply(f"Broadcast sent to {delivered} users.")
        del user_states[user_id]
    elif state.get("mode") in ("revoke_complete_user", "revoke_single_user"):
        target_id = find_saved_user(message.text)
        if not target_id:
            message.reply("User not found. They must have used /start before; send their numeric ID or saved @username.")
            return

        if state["mode"] == "revoke_single_user":
            user_states[user_id] = {"mode": "revoke_single_link", "target_id": target_id}
            message.reply("Now send the media label, link code, or full media link to revoke.")
            return

        target = users_db[target_id]
        recorded_media = list(target.get("delivered_media", []))
        for link_media in target.get("delivered_media_by_link", {}).values():
            recorded_media.extend(link_media)
        # Preserve order while avoiding a second delete attempt for a message.
        recorded_media = list(dict.fromkeys(recorded_media))
        target["authorized"] = False
        target["authorized_links"] = []
        target["pending_links"] = []
        target["delivered_media"] = []
        target["delivered_media_by_link"] = {}
        save_users()  # Access is revoked even if a deletion request later fails.
        deleted = revoke_delivered_media(client, target_id, recorded_media)
        display_name = target.get("name", "User")
        message.reply(
            f"Access revoked for {display_name} (ID: {target_id}). "
            f"Deleted {deleted} tracked media messages. They will need approval again."
        )
        del user_states[user_id]
    elif state.get("mode") == "revoke_single_link":
        link_id = find_link(message.text)
        if not link_id:
            message.reply("Link not found. Send the exact label, link code, or full media link.")
            return

        target_id = state["target_id"]
        target = users_db.get(target_id)
        if not target:
            message.reply("User data is no longer available.")
            del user_states[user_id]
            return

        authorized_links = target.get("authorized_links", [])
        tracked_by_link = target.get("delivered_media_by_link", {})
        if link_id not in authorized_links and link_id not in tracked_by_link:
            message.reply("That user does not currently have access to this link.")
            return

        target["authorized_links"] = [item for item in authorized_links if item != link_id]
        target["pending_links"] = [
            item for item in target.get("pending_links", []) if item != link_id
        ]
        target["authorized"] = bool(target["authorized_links"])
        recorded_media = tracked_by_link.pop(link_id, [])
        save_users()  # Remove permission before attempting message deletion.
        deleted = revoke_delivered_media(client, target_id, recorded_media)
        label = links_db.get(link_id, {}).get("label", link_id)
        message.reply(
            f"Access revoked for '{label}' (ID: {target_id}). "
            f"Deleted {deleted} tracked media messages for this link."
        )
        del user_states[user_id]


print("Bot running...")
app.run()
