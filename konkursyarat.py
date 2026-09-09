import os
import json
import time
import threading
import requests
from datetime import datetime
from flask import Flask, request, jsonify
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ==================== SOZLAMALAR ====================
GLOBAL_CONFIG = {
    'bot_token': '8782186185:AAGUzhiMa12n2txweVjmTTViYrn6X935i7Q',
    'bot_username': 'KonkursYaratBot',
    'admin_id': 6968399046,
    'mongo_uri': 'mongodb+srv://djsjjuebbd_db_user:java2011@cluster0.tboiqeb.mongodb.net/?appName=Cluster0',
    'db_name': 'konkurs_bot_db'
}

# ==================== KESHLASH VA XAVFSIZLIK ====================
MEMORY_STATES = {}
STATE_FILE = "local_states.json"

def save_local_state_file(user_id, state, temp_data):
    try:
        data = {}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data[str(user_id)] = {
            "state": state,
            "temp_data": temp_data,
            "updated_at": str(datetime.utcnow())
        }
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logging.error(f"Local state file write error: {e}")

def load_local_state_file(user_id):
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
            user_id_str = str(user_id)
            if user_id_str in data:
                return data[user_id_str]
    except Exception as e:
        logging.error(f"Local state file read error: {e}")
    return None

def delete_local_state_file(user_id):
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
            user_id_str = str(user_id)
            if user_id_str in data:
                del data[user_id_str]
                with open(STATE_FILE, 'w') as f:
                    json.dump(data, f)
    except Exception as e:
        logging.error(f"Local state file delete error: {e}")

app = Flask(__name__)

# ==================== FORMATTING HELPER FOR PREMIUM EMOJIS ====================
def entities_to_html(text, entities):
    if not text:
        return ""
    if not entities or not isinstance(entities, list):
        return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    
    insertions = {}
    for ent in entities:
        offset = ent.get('offset', 0)
        length = ent.get('length', 0)
        type_ = ent.get('type')
        
        start_tag = ""
        end_tag = ""
        
        if type_ == 'bold':
            start_tag, end_tag = "<b>", "</b>"
        elif type_ == 'italic':
            start_tag, end_tag = "<i>", "</i>"
        elif type_ == 'underline':
            start_tag, end_tag = "<u>", "</u>"
        elif type_ == 'strikethrough':
            start_tag, end_tag = "<s>", "</s>"
        elif type_ == 'code':
            start_tag, end_tag = "<code>", "</code>"
        elif type_ == 'pre':
            start_tag, end_tag = "<pre>", "</pre>"
        elif type_ == 'blockquote':
            start_tag, end_tag = "<blockquote>", "</blockquote>"
        elif type_ == 'text_link':
            url = ent.get('url', '')
            start_tag, end_tag = f'<a href="{url}">', "</a>"
        elif type_ == 'custom_emoji':
            emoji_id = ent.get('custom_emoji_id', '')
            start_tag, end_tag = f'<tg-emoji emoji-id="{emoji_id}">', "</tg-emoji>"
        
        if start_tag:
            if offset not in insertions:
                insertions[offset] = []
            insertions[offset].append((0, start_tag))
            
            end_offset = offset + length
            if end_offset not in insertions:
                insertions[end_offset] = []
            insertions[end_offset].append((1, end_tag))

    utf16_text = text.encode('utf-16-le')
    num_utf16_chars = len(utf16_text) // 2
    
    html_parts = []
    for i in range(num_utf16_chars + 1):
        if i in insertions:
            sorted_tags = sorted(insertions[i], key=lambda x: x[0], reverse=True)
            for _, tag in sorted_tags:
                html_parts.append(tag)
        
        if i < num_utf16_chars:
            char_bytes = utf16_text[i*2 : (i+1)*2]
            char = char_bytes.decode('utf-16-le')
            if char == '&':
                html_parts.append('&amp;')
            elif char == '<':
                html_parts.append('&lt;')
            elif char == '>':
                html_parts.append('&gt;')
            else:
                html_parts.append(char)
                
    return "".join(html_parts)

# ==================== TELEGRAM API HELPER ====================
def telegram_api(method, params=None):
    if params is None:
        params = {}
    bot_token = GLOBAL_CONFIG['bot_token']
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    
    try:
        response = requests.post(url, data=params, timeout=12)
        return response.json()
    except Exception as e:
        logging.error(f"Telegram API Error ({method}): {e}")
        return None

# ==================== MONGO DATABASE CLASS ====================
class Database:
    _instance = None

    def __init__(self):
        self.client = MongoClient(
            GLOBAL_CONFIG['mongo_uri'],
            tls=True,
            tlsAllowInvalidCertificates=True
        )
        self.db = self.client[GLOBAL_CONFIG['db_name']]
        self.init_indexes()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def init_indexes(self):
        try:
            self.db.processed_updates.create_index("created_at", expireAfterSeconds=3600)
            self.db.processed_updates.create_index("update_id", unique=True)
            self.db.channels.create_index("channel_id", unique=True)
            self.db.contests.create_index("id", unique=True)
            self.db.participants.create_index("id", unique=True)
            self.db.participants.create_index([("contest_id", ASCENDING), ("user_id", ASCENDING)], unique=True)
            self.db.votes.create_index("id", unique=True)
            self.db.votes.create_index([("contest_id", ASCENDING), ("voter_id", ASCENDING)], unique=True)
            self.db.user_states.create_index("user_id")
            self.db.mandatory_channels.create_index("id", unique=True)
            self.db.mandatory_channels.create_index("channel_id", unique=True)
        except Exception as e:
            logging.warning(f"Index creation warning: {e}")

    def get_next_sequence(self, name):
        ret = self.db.counters.find_one_and_update(
            {"_id": name},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER
        )
        return ret["seq"]

    def is_update_processed(self, update_id):
        if not update_id:
            return False
        update_id_str = str(update_id)
        try:
            self.db.processed_updates.insert_one({
                "update_id": update_id_str,
                "created_at": datetime.utcnow()
            })
            return False
        except DuplicateKeyError:
            return True
        except Exception:
            return False

    def save_channel(self, channel_id, title, username=None):
        channel_id_str = str(channel_id)
        self.db.channels.update_one(
            {"channel_id": channel_id_str},
            {"$set": {
                "channel_id": channel_id_str,
                "title": title,
                "username": username or ""
            },
            "$setOnInsert": {
                "contests_count": 0,
                "created_at": datetime.utcnow()
            }},
            upsert=True
        )

    def remove_channel(self, channel_id):
        self.db.channels.delete_one({"channel_id": str(channel_id)})

    def get_channel(self, channel_id):
        return self.db.channels.find_one({"channel_id": str(channel_id)})

    def get_all_channels(self):
        channels_map = {}
        result = []

        for ch in self.db.channels.find().sort("created_at", DESCENDING):
            c_id = str(ch.get('channel_id'))
            if c_id and c_id not in channels_map:
                result.append(ch)
                channels_map[c_id] = True

        contest_channel_ids = self.db.contests.distinct("channel_id")
        for c_id in contest_channel_ids:
            if c_id:
                c_id_str = str(c_id)
                if c_id_str not in channels_map:
                    result.append({'channel_id': c_id_str, 'title': f'Kanal {c_id_str}', 'username': ''})
                    channels_map[c_id_str] = True

        for m_ch in self.db.mandatory_channels.find():
            m_id_str = str(m_ch.get('channel_id'))
            if m_id_str and m_id_str not in channels_map:
                result.append(m_ch)
                channels_map[m_id_str] = True

        return result

    def get_top_channels(self, limit=10):
        pipeline = [
            {
                "$lookup": {
                    "from": "contests",
                    "localField": "channel_id",
                    "foreignField": "channel_id",
                    "as": "contests_list"
                }
            },
            {
                "$addFields": {
                    "contest_ids": "$contests_list.id",
                    "total_contests": {"$size": "$contests_list"},
                    "active_contests": {
                        "$size": {
                            "$filter": {
                                "input": "$contests_list",
                                "as": "c",
                                "cond": {"$eq": ["$$c.status", "active"]}
                            }
                        }
                    }
                }
            },
            {
                "$lookup": {
                    "from": "participants",
                    "localField": "contest_ids",
                    "foreignField": "contest_id",
                    "as": "participants_list"
                }
            },
            {
                "$addFields": {
                    "total_participants": {"$size": "$participants_list"},
                    "total_votes": {"$sum": "$participants_list.votes_count"}
                }
            },
            {
                "$sort": {
                    "total_contests": -1,
                    "total_votes": -1,
                    "total_participants": -1
                }
            },
            {"$limit": limit}
        ]
        return list(self.db.channels.aggregate(pipeline))

    def create_contest(self, channel_id, post_message_id, prize=None):
        try:
            contest_id = self.get_next_sequence("contest_id")
            channel_id_str = str(channel_id)
            self.db.contests.insert_one({
                "id": contest_id,
                "channel_id": channel_id_str,
                "post_message_id": int(post_message_id),
                "prize": prize or "Sir 🤫",
                "status": "active",
                "created_at": datetime.utcnow(),
                "ended_at": None
            })
            self.db.channels.update_one(
                {"channel_id": channel_id_str},
                {"$inc": {"contests_count": 1}}
            )
            return contest_id
        except Exception as e:
            logging.error(f"Create Contest Error: {e}")
            return False

    def get_active_contest_by_channel(self, channel_id):
        return self.db.contests.find_one(
            {"channel_id": str(channel_id), "status": "active"},
            sort=[("id", DESCENDING)]
        )

    def get_latest_contest_by_channel(self, channel_id):
        channel_id_str = str(channel_id)
        contest = self.db.contests.find_one({"channel_id": channel_id_str}, sort=[("id", DESCENDING)])
        if contest:
            ch = self.get_channel(channel_id_str)
            contest['channel_title'] = ch.get('title', 'Kanal') if ch else 'Kanal'
            contest['channel_username'] = ch.get('username', '') if ch else ''
        return contest

    def get_contest_by_id(self, contest_id):
        contest = self.db.contests.find_one({"id": int(contest_id)})
        if contest:
            ch = self.get_channel(contest.get('channel_id'))
            contest['channel_title'] = ch.get('title', 'Kanal') if ch else 'Kanal'
            contest['channel_username'] = ch.get('username', '') if ch else ''
        return contest

    def get_contest_by_post_message(self, channel_id, post_message_id):
        contest = self.db.contests.find_one({
            "channel_id": str(channel_id),
            "post_message_id": int(post_message_id)
        })
        if contest:
            ch = self.get_channel(contest.get('channel_id'))
            contest['channel_title'] = ch.get('title', 'Kanal') if ch else 'Kanal'
            contest['channel_username'] = ch.get('username', '') if ch else ''
        return contest

    def end_contest(self, contest_id):
        self.db.contests.update_one(
            {"id": int(contest_id)},
            {"$set": {"status": "ended", "ended_at": datetime.utcnow()}}
        )

    def add_participant(self, contest_id, user_id, user_name, media_type='text', file_id=None, caption=None):
        try:
            part_id = self.get_next_sequence("participant_id")
            self.db.participants.insert_one({
                "id": part_id,
                "contest_id": int(contest_id),
                "user_id": str(user_id),
                "user_name": str(user_name),
                "media_type": str(media_type),
                "file_id": file_id,
                "caption": caption,
                "votes_count": 0,
                "created_at": datetime.utcnow()
            })
            return part_id
        except DuplicateKeyError:
            return False
        except Exception as e:
            logging.error(f"Add Participant Error: {e}")
            return False

    def get_participant(self, contest_id, user_id):
        return self.db.participants.find_one({
            "contest_id": int(contest_id),
            "user_id": str(user_id)
        })

    def get_participant_by_id(self, participant_id):
        return self.db.participants.find_one({"id": int(participant_id)})

    def get_participants_by_contest(self, contest_id):
        return list(self.db.participants.find({"contest_id": int(contest_id)}).sort([
            ("votes_count", DESCENDING),
            ("id", ASCENDING)
        ]))

    def get_contest_participants(self, contest_id):
        return self.get_participants_by_contest(contest_id)

    def add_vote(self, contest_id, participant_id, voter_id, voter_username=None, voter_name=None):
        if self.has_voted(contest_id, voter_id):
            return False
        try:
            vote_id = self.get_next_sequence("vote_id")
            self.db.votes.insert_one({
                "id": vote_id,
                "contest_id": int(contest_id),
                "participant_id": int(participant_id),
                "voter_id": str(voter_id),
                "voter_username": voter_username,
                "voter_name": voter_name,
                "created_at": datetime.utcnow()
            })
            self.db.participants.update_one(
                {"id": int(participant_id)},
                {"$inc": {"votes_count": 1}}
            )
            return True
        except DuplicateKeyError:
            return False
        except Exception as e:
            logging.error(f"Add Vote Error: {e}")
            return False

    def get_votes_by_participant(self, participant_id):
        return list(self.db.votes.find({"participant_id": int(participant_id)}).sort("id", ASCENDING))

    def has_voted(self, contest_id, voter_id):
        res = self.db.votes.find_one({
            "contest_id": int(contest_id),
            "voter_id": str(voter_id)
        })
        return bool(res)

    def set_state(self, user_id, state, temp_data=None):
        user_id_str = str(user_id)
        MEMORY_STATES[user_id_str] = {
            "state": state,
            "temp_data": temp_data
        }
        save_local_state_file(user_id_str, state, temp_data)
        try:
            self.db.user_states.delete_many({
                "$or": [
                    {"user_id": user_id_str},
                    {"user_id": int(user_id) if str(user_id).isdigit() else user_id}
                ]
            })
            temp_data_json = json.dumps(temp_data) if isinstance(temp_data, (dict, list)) else temp_data
            self.db.user_states.insert_one({
                "user_id": user_id_str,
                "state": state,
                "temp_data": temp_data_json,
                "updated_at": datetime.utcnow()
            })
        except Exception as e:
            logging.error(f"DB set_state error: {e}")

    def get_state(self, user_id):
        user_id_str = str(user_id)
        if user_id_str in MEMORY_STATES:
            mem = MEMORY_STATES[user_id_str]
            return {
                "user_id": user_id_str,
                "state": mem["state"],
                "temp_data": mem.get("temp_data"),
                "temp_data_decoded": mem.get("temp_data") if isinstance(mem.get("temp_data"), dict) else None
            }
        
        local_val = load_local_state_file(user_id_str)
        if local_val:
            MEMORY_STATES[user_id_str] = {
                "state": local_val["state"],
                "temp_data": local_val["temp_data"]
            }
            return {
                "user_id": user_id_str,
                "state": local_val["state"],
                "temp_data": local_val["temp_data"],
                "temp_data_decoded": local_val["temp_data"] if isinstance(local_val["temp_data"], dict) else None
            }

        try:
            query = {
                "$or": [
                    {"user_id": user_id_str},
                    {"user_id": int(user_id) if str(user_id).isdigit() else user_id}
                ]
            }
            row = self.db.user_states.find_one(query)
            if row and row.get('state'):
                td_decoded = None
                if row.get('temp_data'):
                    try:
                        td_decoded = json.loads(row['temp_data'])
                    except Exception:
                        td_decoded = row['temp_data']
                
                MEMORY_STATES[user_id_str] = {
                    "state": row['state'],
                    "temp_data": td_decoded
                }
                save_local_state_file(user_id_str, row['state'], td_decoded)
                
                row['temp_data_decoded'] = td_decoded
                return row
        except Exception as e:
            logging.error(f"DB get_state error: {e}")
            
        return False

    def clear_state(self, user_id):
        user_id_str = str(user_id)
        if user_id_str in MEMORY_STATES:
            del MEMORY_STATES[user_id_str]
        delete_local_state_file(user_id_str)
        try:
            query = {
                "$or": [
                    {"user_id": user_id_str},
                    {"user_id": int(user_id) if str(user_id).isdigit() else user_id}
                ]
            }
            self.db.user_states.delete_many(query)
        except Exception as e:
            logging.error(f"DB clear_state error: {e}")

    def add_mandatory_channel(self, channel_id, title, username=None, invite_link=None):
        channel_id_str = str(channel_id)
        existing = self.db.mandatory_channels.find_one({"channel_id": channel_id_str})
        if existing:
            self.db.mandatory_channels.update_one(
                {"channel_id": channel_id_str},
                {"$set": {
                    "title": title,
                    "username": username,
                    "invite_link": invite_link
                }}
            )
            return existing["id"]
        else:
            mand_id = self.get_next_sequence("mand_id")
            self.db.mandatory_channels.insert_one({
                "id": mand_id,
                "channel_id": channel_id_str,
                "title": title,
                "username": username,
                "invite_link": invite_link,
                "created_at": datetime.utcnow()
            })
            return mand_id

    def remove_mandatory_channel(self, id_num):
        self.db.mandatory_channels.delete_one({"id": int(id_num)})

    def get_mandatory_channels(self):
        return list(self.db.mandatory_channels.find().sort("id", ASCENDING))

    def get_mandatory_channel_by_id(self, id_num):
        return self.db.mandatory_channels.find_one({"id": int(id_num)})

    def get_stats(self):
        return {
            'total_channels': self.db.channels.count_documents({}),
            'total_contests': self.db.contests.count_documents({}),
            'active_contests': self.db.contests.count_documents({"status": "active"}),
            'ended_contests': self.db.contests.count_documents({"status": "ended"}),
            'total_participants': self.db.participants.count_documents({}),
            'total_votes': self.db.votes.count_documents({}),
            'unique_voters': len(self.db.votes.distinct("voter_id")),
            'mandatory_channels': self.db.mandatory_channels.count_documents({})
        }

# ==================== BOT CLASS ====================
class KonkursBot:
    def __init__(self):
        self.config = GLOBAL_CONFIG
        self.bot_token = self.config['bot_token']
        self.bot_username = self.config['bot_username'].lstrip('@') if self.config.get('bot_username') else ''
        self.db = Database.get_instance()

        if not self.bot_username:
            me = self.telegram_api('getMe')
            if me and me.get('ok') and me.get('result', {}).get('username'):
                self.bot_username = me['result']['username']

    def telegram_api(self, method, params=None):
        return telegram_api(method, params)

    def is_duplicate_update(self, update_id):
        if not update_id:
            return False
        return self.db.is_update_processed(update_id)

    def format_contest_results(self, participants):
        res_text = "<blockquote>🏆 <b>KONKURS NATIJALARI</b> 🏆\n\n"
        if not participants:
            res_text += "<i>Hech kim ishtirok etmadi.</i>"
        else:
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            for idx, p in enumerate(participants):
                icon = medals[idx] if idx < len(medals) else f"{idx + 1}."
                p_name = p.get('user_name') or 'Ishtirokchi'
                res_text += f"{icon} {idx + 1}-OʻRIN: <b>{p_name}</b> — <b>{int(p['votes_count'])} OVOZ 🗳️</b>\n"
        res_text += "\n🎉 <b>Konkurs rasman yakunlandi! Barcha g'oliblarni tabriklaymiz!</b></blockquote>"
        return res_text

    def handle_update(self, update):
        try:
            update_id = update.get('update_id')
            if update_id and self.is_duplicate_update(update_id):
                return

            if 'my_chat_member' in update:
                self.handle_my_chat_member(update['my_chat_member'])
                return

            if 'chat_member' in update:
                self.handle_my_chat_member(update['chat_member'])
                return

            if 'channel_post' in update or 'edited_channel_post' in update:
                post = update.get('channel_post') or update.get('edited_channel_post')
                self.handle_channel_post(post)
                return

            if 'message' in update:
                self.handle_message(update['message'])
                return

            if 'callback_query' in update:
                self.handle_callback_query(update['callback_query'])
                return
        except Exception as e:
            logging.error(f"Error handling update: {e}", exc_info=True)

    def handle_my_chat_member(self, data):
        chat = data.get('chat')
        new_member = data.get('new_chat_member')
        if not chat or not new_member:
            return

        chat_id = chat['id']
        title = chat.get('title', 'Kanal')
        username = chat.get('username', '')
        status = new_member.get('status', '')

        if status in ['administrator', 'member']:
            self.db.save_channel(chat_id, title, username)
        elif status in ['left', 'kicked', 'restricted']:
            self.db.remove_channel(chat_id)

    def handle_channel_post(self, post):
        chat_id = post['chat']['id']
        message_id = post['message_id']
        title = post['chat'].get('title', 'Kanal')
        username = post['chat'].get('username', '')
        text = post.get('text') or post.get('caption') or ''

        self.db.save_channel(chat_id, title, username)

        if '#boshlash' in text.lower():
            contest_id = self.db.create_contest(chat_id, message_id, prize="Sir 🤫")
            if not contest_id or contest_id <= 0:
                self.telegram_api('sendMessage', {
                    'chat_id': chat_id,
                    'text': "⚠️ <b>Xatolik:</b> Konkurs ma'lumotlar bazasiga saqlanmadi. Iltimos MongoDB ulanishini tekshiring!",
                    'parse_mode': 'HTML'
                })
                return

            join_url = f"https://t.me/{self.bot_username}?start=join_{contest_id}"

            post_text = "<blockquote>🏆 <b>BATL Boshlandi🥳</b>\n\n"
            post_text += "❗ Konkurs shartlari shu kanalga obuna bo'lish va do'stlaringiz sizga ovoz berishini so'rashdan iborat. Agar kanalga qo'shilib ovoz berib chiqib ketsa ovozi avtomatik olib tashlanadi ⛔\n\n"
            post_text += "🎁 <b>Yutuq:</b> Sir 🤫\n\n"
            post_text += "➕ Konkursga qo'shilish uchun quyidagi tugmani bosing 👇</blockquote>"

            keyboard = {
                'inline_keyboard': [
                    [{'text': "🟢 Konkursga Qo'shilish ➕", 'url': join_url}],
                    [{'text': "🔴 Natijalar 📊", 'callback_data': f"results_{contest_id}"}]
                ]
            }

            self.telegram_api('editMessageText', {
                'chat_id': chat_id,
                'message_id': message_id,
                'text': post_text,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })
            return

        if '#end' in text.lower():
            reply_msg_id = post.get('reply_to_message', {}).get('message_id')
            contest = None

            if reply_msg_id:
                contest = self.db.get_contest_by_post_message(chat_id, reply_msg_id)

            if not contest:
                active_contest = self.db.get_active_contest_by_channel(chat_id)
                if active_contest and active_contest.get('post_message_id') == reply_msg_id:
                    contest = active_contest

            if not contest:
                self.telegram_api('sendMessage', {
                    'chat_id': chat_id,
                    'text': "⚠️ <b>Iltimos, konkursni yakunlash uchun aynan o'sha konkurs postiga reply (javob) qilib #end yuboring!</b>",
                    'parse_mode': 'HTML',
                    'reply_to_message_id': message_id
                })
                return

            self.telegram_api('deleteMessage', {
                'chat_id': chat_id,
                'message_id': message_id
            })

            target_reply_id = contest.get('post_message_id') or reply_msg_id

            if contest.get('status') == 'ended':
                self.telegram_api('sendMessage', {
                    'chat_id': chat_id,
                    'text': "❌ <b>Ushbu konkurs allaqachon yakunlangan!</b>",
                    'parse_mode': 'HTML',
                    'reply_to_message_id': target_reply_id
                })
                return

            contest_id = contest['id']
            self.db.end_contest(contest_id)

            participants = self.db.get_participants_by_contest(contest_id)
            res_text = self.format_contest_results(participants)

            send_params = {
                'chat_id': chat_id,
                'text': res_text,
                'parse_mode': 'HTML'
            }
            if target_reply_id:
                send_params['reply_to_message_id'] = target_reply_id

            self.telegram_api('sendMessage', send_params)
            return

    def handle_message(self, message):
        chat = message.get('chat', {})
        chat_id = chat.get('id')
        chat_type = chat.get('type', 'private')

        # Kanal yoki guruhdan kelgan xabarlarni bot shaxsiy menyu sifatida qabul qilmasligi uchun filtr
        if chat_type != 'private':
            return

        from_user = message.get('from')
        if not from_user:
            return

        user_id = from_user['id']
        text = message.get('text', '').strip()

        user_state = self.db.get_state(user_id)

        if text == '/cancel':
            self.db.clear_state(user_id)
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': "❌ Amaliyot bekor qilindi.",
                'parse_mode': 'HTML'
            })
            self.send_main_menu(chat_id)
            return

        if text == '/admin' and self.is_admin(user_id):
            self.send_admin_panel(chat_id)
            return

        if user_state and (not text or not text.startswith('/start')):
            state_name = user_state.get('state')

            if state_name == 'awaiting_contest_prize':
                self.process_contest_prize_input(chat_id, user_id, message)
                return

            if state_name == 'awaiting_create_battle_post':
                self.process_create_battle_post_input(chat_id, user_id, message)
                return

            if state_name and state_name.startswith('awaiting_submission_'):
                contest_id = int(state_name.replace('awaiting_submission_', ''))
                self.process_participant_submission(message, contest_id)
                return

            if state_name == 'awaiting_check_channel':
                self.process_check_channel_input(chat_id, user_id, message)
                return

            if state_name == 'awaiting_add_mand_channel':
                self.process_add_mandatory_channel_input(chat_id, user_id, message)
                return

            if state_name == 'awaiting_channel_broadcast_msg':
                self.process_channel_broadcast_input(chat_id, user_id, message)
                return

        unsubscribed_mandatory = self.check_global_mandatory_subscription(user_id)
        if unsubscribed_mandatory:
            self.prompt_mandatory_subscription(chat_id, unsubscribed_mandatory)
            return

        if text.startswith('/start'):
            parts = text.split(' ')
            param = parts[1].strip() if len(parts) > 1 else ''

            if not param:
                self.send_main_menu(chat_id)
                return

            if param.startswith('join_') or param.startswith('join'):
                contest_id = int(''.join(filter(str.isdigit, param)) or 0)
                self.prompt_join_contest(chat_id, user_id, contest_id, from_user)
                return

            if param.startswith('contest_') or param.startswith('results_'):
                contest_id = int(''.join(filter(str.isdigit, param)) or 0)
                self.send_contest_details(chat_id, contest_id)
                return

            if param.startswith('vote_'):
                vote_parts = param.split('_')
                if len(vote_parts) >= 3:
                    contest_id = int(vote_parts[1])
                    participant_id = int(vote_parts[2])
                    self.process_vote(chat_id, user_id, contest_id, participant_id, from_user)
                    return

            self.send_main_menu(chat_id)
            return

        self.send_main_menu(chat_id)

    def send_main_menu(self, chat_id):
        text = "✅ <b>Xush kelibsiz!</b>\n\n"
        text += "⚡ <b>Bot ishga tushdi!</b>\n\n"
        text += "📑 <b>Konkurs qanday ishlaydi:</b>\n"
        text += "• Botni istalgan kanalingizga <b>admin</b> qilib qo'shing\n"
        text += "• 🚀 <b>Batl Yaratish</b> tugmasini bosing yoki kanalga #boshlash so'zi bilan post yuboring\n"
        text += "• Bot avtomatik ravishda \"Qo'shilish\" va \"Natijalar\" tugmalarini biriktiradi\n\n"
        text += "👇 <b>Kerakli bo'limni tanlang:</b>"

        keyboard = {
            'inline_keyboard': [
                [
                    {'text': "🚀 Batl Yaratish", 'callback_data': "create_battle"}
                ],
                [
                    {'text': "🏆 Top 10 kanal", 'callback_data': "top_channels"},
                    {'text': "🔍 Ovoz batl tekshirish", 'callback_data': "check_battle"}
                ]
            ]
        }

        if self.is_admin(chat_id):
            keyboard['inline_keyboard'].append([
                {'text': "👑 Admin Panel", 'callback_data': "admin_panel"}
            ])

        self.telegram_api('sendMessage', {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML',
            'reply_markup': json.dumps(keyboard)
        })

    def edit_to_main_menu(self, chat_id, message_id):
        text = "✅ <b>Xush kelibsiz!</b>\n\n"
        text += "⚡ <b>Bot ishga tushdi!</b>\n\n"
        text += "📑 <b>Konkurs qanday ishlaydi:</b>\n"
        text += "• Botni istalgan kanalingizga <b>admin</b> qilib qo'shing\n"
        text += "• 🚀 <b>Batl Yaratish</b> tugmasini bosing yoki kanalga #boshlash so'zi bilan post yuboring\n"
        text += "• Bot avtomatik ravishda \"Qo'shilish\" va \"Natijalar\" tugmalarini biriktiradi\n\n"
        text += "👇 <b>Kerakli bo'limni tanlang:</b>"

        keyboard = {
            'inline_keyboard': [
                [
                    {'text': "🚀 Batl Yaratish", 'callback_data': "create_battle"}
                ],
                [
                    {'text': "🏆 Top 10 kanal", 'callback_data': "top_channels"},
                    {'text': "🔍 Ovoz batl tekshirish", 'callback_data': "check_battle"}
                ]
            ]
        }

        if self.is_admin(chat_id):
            keyboard['inline_keyboard'].append([
                {'text': "👑 Admin Panel", 'callback_data': "admin_panel"}
            ])

        res = self.telegram_api('editMessageText', {
            'chat_id': chat_id,
            'message_id': message_id,
            'text': text,
            'parse_mode': 'HTML',
            'reply_markup': json.dumps(keyboard)
        })

        if not res or not res.get('ok'):
            self.send_main_menu(chat_id)

    def handle_callback_query(self, cb):
        cb_id = cb['id']
        message = cb.get('message', {})
        chat = message.get('chat', {})
        chat_id = chat.get('id')
        chat_type = chat.get('type', 'private')
        user_id = cb['from']['id']
        data = cb['data']

        # Kanal yoki guruhdan kelgan callback query'larni tekshirish (Faqat Natijalar / konkurs tugmalariga ruxsat)
        if chat_type != 'private':
            if data.startswith('results_') or data.startswith('contest_'):
                contest_id = int(''.join(filter(str.isdigit, data)) or 0)
                contest = self.db.get_contest_by_id(contest_id)

                if not contest:
                    self.telegram_api('answerCallbackQuery', {
                        'callback_query_id': cb_id,
                        'text': "❌ Konkurs topilmadi.",
                        'show_alert': True
                    })
                    return

                if contest.get('status') == 'ended':
                    self.telegram_api('answerCallbackQuery', {
                        'callback_query_id': cb_id,
                        'text': "🔴 Ushbu konkurs allaqachon yakunlangan!",
                        'show_alert': True
                    })
                    return

                channel_id = contest.get('channel_id')
                if not self.is_channel_admin(user_id, channel_id):
                    self.telegram_api('answerCallbackQuery', {
                        'callback_query_id': cb_id,
                        'text': "❌ Ushbu tugmani faqat kanal adminlari yoki egasi bosishi mumkin!",
                        'show_alert': True
                    })
                    return

                self.telegram_api('answerCallbackQuery', {
                    'callback_query_id': cb_id,
                    'text': "✅ Natijalar e'lon qilindi va konkurs yakunlandi!"
                })

                participants = self.db.get_participants_by_contest(contest_id)
                self.db.end_contest(contest_id)

                res_text = self.format_contest_results(participants)

                target_chat_id = contest.get('channel_id') or chat_id
                reply_msg_id = contest.get('post_message_id')

                send_params = {
                    'chat_id': target_chat_id,
                    'text': res_text,
                    'parse_mode': 'HTML'
                }
                if reply_msg_id:
                    send_params['reply_to_message_id'] = reply_msg_id

                self.telegram_api('sendMessage', send_params)
                return
            else:
                self.telegram_api('answerCallbackQuery', {
                    'callback_query_id': cb_id,
                    'text': "⚠️ Ushbu tugmadan foydalanish uchun botga shaxsiy xabar yuboring!",
                    'show_alert': True
                })
                return

        if data == 'create_battle':
            self.db.set_state(user_id, 'awaiting_contest_prize')

            msg = "Batl yaratish boshlandi\n"
            msg += "Yutuq nomi nima \n"
            msg += "Yozing \n"
            msg += "Musol uchun bu 👆🏻\n\n"
            msg += "❌ Bekor qilish uchun /cancel ni bosing."

            keyboard = {
                'inline_keyboard': [
                    [{'text': "❌ Bekor qilish", 'callback_data': "back_to_main"}]
                ]
            }

            self.telegram_api('editMessageText', {
                'chat_id': chat_id,
                'message_id': message.get('message_id'),
                'text': msg,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })
            self.telegram_api('answerCallbackQuery', {'callback_query_id': cb_id})
            return

        if data == 'check_global_sub':
            unsubscribed = self.check_global_mandatory_subscription(user_id)
            if not unsubscribed:
                self.telegram_api('answerCallbackQuery', {
                    'callback_query_id': cb_id,
                    'text': "✅ Obuna tasdiqlandi!",
                    'show_alert': False
                })
                self.edit_to_main_menu(chat_id, message.get('message_id'))
            else:
                self.telegram_api('answerCallbackQuery', {
                    'callback_query_id': cb_id,
                    'text': "⚠️ Hali barcha kanallarga obuna bo'lmadingiz!",
                    'show_alert': True
                })
                self.prompt_mandatory_subscription(chat_id, unsubscribed, message.get('message_id'))
            return

        if data == 'admin_panel' and self.is_admin(user_id):
            self.db.clear_state(user_id)
            self.send_admin_panel(chat_id, message.get('message_id'))
            self.telegram_api('answerCallbackQuery', {'callback_query_id': cb_id})
            return

        if data == 'admin_stats' and self.is_admin(user_id):
            self.send_admin_stats(chat_id, message.get('message_id'))
            self.telegram_api('answerCallbackQuery', {'callback_query_id': cb_id})
            return

        if data == 'admin_channels_menu' and self.is_admin(user_id):
            self.db.clear_state(user_id)
            self.send_admin_channels_menu(chat_id, message.get('message_id'))
            self.telegram_api('answerCallbackQuery', {'callback_query_id': cb_id})
            return

        if data == 'admin_list_channels' and self.is_admin(user_id):
            self.send_admin_list_channels(chat_id, message.get('message_id'))
            self.telegram_api('answerCallbackQuery', {'callback_query_id': cb_id})
            return

        if data == 'admin_add_channel' and self.is_admin(user_id):
            self.db.set_state(user_id, 'awaiting_add_mand_channel')

            msg = "📢 <b>MAJBURIY OBUNAGA KANAL QO'SHISH</b>\n\n"
            msg += "Iltimos, kanaldagi istalgan postni <b>forward</b> qilib yuboring yoki kanal username-ini yuboring (masalan: <code>@education_coders</code>).\n\n"
            msg += "⚠️ <i>Bot o'sha kanalda <b>Admin</b> qilinganiga ishonch hosil qiling!</i>"

            keyboard = {
                'inline_keyboard': [
                    [{'text': "❌ Bekor qilish", 'callback_data': "admin_channels_menu"}]
                ]
            }

            self.telegram_api('editMessageText', {
                'chat_id': chat_id,
                'message_id': message.get('message_id'),
                'text': msg,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })
            self.telegram_api('answerCallbackQuery', {'callback_query_id': cb_id})
            return

        if data == 'admin_broadcast_channels' and self.is_admin(user_id):
            self.db.set_state(user_id, 'awaiting_channel_broadcast_msg')

            msg = "📣 <b>KANALLARGA XABAR YUBORISH</b>\n\n"
            msg += "Iltimos, bot admin bo'lgan barcha kanallarga yubormoqchi bo'lgan xabaringizni (matn, rasm, video, audio) yuboring:\n\n"
            msg += "❌ Bekor qilish uchun quyidagi tugmani bosing:"

            keyboard = {
                'inline_keyboard': [
                    [{'text': "❌ Bekor qilish", 'callback_data': "admin_panel"}]
                ]
            }

            self.telegram_api('editMessageText', {
                'chat_id': chat_id,
                'message_id': message.get('message_id'),
                'text': msg,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })
            self.telegram_api('answerCallbackQuery', {'callback_query_id': cb_id})
            return

        if data == 'admin_del_channel' and self.is_admin(user_id):
            self.send_admin_delete_channel_menu(chat_id, message.get('message_id'))
            self.telegram_api('answerCallbackQuery', {'callback_query_id': cb_id})
            return

        if data.startswith('del_mand_') and self.is_admin(user_id):
            mand_id = int(data.replace('del_mand_', ''))
            self.db.remove_mandatory_channel(mand_id)

            self.telegram_api('answerCallbackQuery', {
                'callback_query_id': cb_id,
                'text': "✅ Kanal o'chirildi!",
                'show_alert': True
            })
            self.send_admin_delete_channel_menu(chat_id, message.get('message_id'))
            return

        unsubscribed_mandatory = self.check_global_mandatory_subscription(user_id)
        if unsubscribed_mandatory:
            self.telegram_api('answerCallbackQuery', {
                'callback_query_id': cb_id,
                'text': "⚠️ Avval majburiy kanallarga obuna bo'ling!",
                'show_alert': True
            })
            self.prompt_mandatory_subscription(chat_id, unsubscribed_mandatory, message.get('message_id'))
            return

        if data == 'top_channels':
            self.send_top_channels(chat_id, message.get('message_id'))
            self.telegram_api('answerCallbackQuery', {'callback_query_id': cb_id})
            return

        if data == 'check_battle':
            self.db.set_state(user_id, 'awaiting_check_channel')
            msg = "🔍 <b>OVOZ BATL TEKSHIRISH</b> 🔍\n\n"
            msg += "Iltimos, kanaldagi konkurs postini (#boshlash bilan boshlangan) forward qilib yuboring.\n\n"
            msg += "❌ Bekor qilish uchun /cancel yoki quyidagi tugmani bosing"

            keyboard = {
                'inline_keyboard': [
                    [{'text': "❌ Bekor qilish", 'callback_data': "back_to_main"}]
                ]
            }

            self.telegram_api('editMessageText', {
                'chat_id': chat_id,
                'message_id': message.get('message_id'),
                'text': msg,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })
            self.telegram_api('answerCallbackQuery', {'callback_query_id': cb_id})
            return

        if data == 'back_to_main':
            self.db.clear_state(user_id)
            self.edit_to_main_menu(chat_id, message.get('message_id'))
            self.telegram_api('answerCallbackQuery', {'callback_query_id': cb_id})
            return

        if data.startswith('vote_'):
            parts = data.split('_')
            if len(parts) >= 3:
                contest_id = int(parts[1])
                participant_id = int(parts[2])

                contest = self.db.get_contest_by_id(contest_id)
                if not contest:
                    self.telegram_api('answerCallbackQuery', {
                        'callback_query_id': cb_id,
                        'text': "❌ Konkurs topilmadi.",
                        'show_alert': True
                    })
                    return

                if contest.get('status') != 'active':
                    self.telegram_api('answerCallbackQuery', {
                        'callback_query_id': cb_id,
                        'text': "🔴 Ushbu konkurs allaqachon yakunlangan! Ovoz berish to'xtatilgan.",
                        'show_alert': True
                    })
                    return

                participant = self.db.get_participant_by_id(participant_id)
                if participant and str(participant.get('user_id')) == str(user_id):
                    self.telegram_api('answerCallbackQuery', {
                        'callback_query_id': cb_id,
                        'text': "❌ O'zingizga ovoz bera olmaysiz!",
                        'show_alert': True
                    })
                    return

                if contest.get('channel_id'):
                    if not self.check_user_subscribed(user_id, contest['channel_id']):
                        ch_name = contest.get('channel_title', 'Kanal')
                        self.telegram_api('answerCallbackQuery', {
                            'callback_query_id': cb_id,
                            'text': f"⚠️ Ovoz berish uchun «{ch_name}» kanaliga obuna bo'lishingiz kerak!",
                            'show_alert': True
                        })
                        return

                voter_username = cb['from'].get('username')
                voter_name = cb['from'].get('first_name', '')
                if not voter_username and cb['from'].get('last_name'):
                    voter_name += ' ' + cb['from']['last_name']

                success = self.db.add_vote(contest_id, participant_id, user_id, voter_username, voter_name)

                if success:
                    self.telegram_api('answerCallbackQuery', {
                        'callback_query_id': cb_id,
                        'text': "🎉 Ovozingiz qabul qilindi!",
                        'show_alert': True
                    })
                    self.send_contest_details(chat_id, contest_id, message.get('message_id'))
                else:
                    self.telegram_api('answerCallbackQuery', {
                        'callback_query_id': cb_id,
                        'text': "⚠️ Siz allaqachon ushbu konkursda ovoz bergansiz!",
                        'show_alert': True
                    })
                return

        if data.startswith('results_') or data.startswith('contest_'):
            contest_id = int(''.join(filter(str.isdigit, data)) or 0)
            contest = self.db.get_contest_by_id(contest_id)

            if not contest:
                self.telegram_api('answerCallbackQuery', {
                    'callback_query_id': cb_id,
                    'text': "❌ Konkurs topilmadi.",
                    'show_alert': True
                })
                return

            if contest.get('status') == 'ended':
                self.telegram_api('answerCallbackQuery', {
                    'callback_query_id': cb_id,
                    'text': "🔴 Ushbu konkurs allaqachon yakunlangan!",
                    'show_alert': True
                })
                return

            channel_id = contest.get('channel_id')
            if not self.is_channel_admin(user_id, channel_id):
                self.telegram_api('answerCallbackQuery', {
                    'callback_query_id': cb_id,
                    'text': "❌ Ushbu tugmani faqat kanal adminlari yoki egasi bosishi mumkin!",
                    'show_alert': True
                })
                return

            self.telegram_api('answerCallbackQuery', {
                'callback_query_id': cb_id,
                'text': "✅ Natijalar e'lon qilindi va konkurs yakunlandi!"
            })

            participants = self.db.get_participants_by_contest(contest_id)
            self.db.end_contest(contest_id)

            res_text = self.format_contest_results(participants)

            target_chat_id = contest.get('channel_id') or chat_id
            reply_msg_id = contest.get('post_message_id')

            send_params = {
                'chat_id': target_chat_id,
                'text': res_text,
                'parse_mode': 'HTML'
            }
            if reply_msg_id:
                send_params['reply_to_message_id'] = reply_msg_id

            self.telegram_api('sendMessage', send_params)
            return

    def process_contest_prize_input(self, chat_id, user_id, message):
        prize_text = message.get('text', '').strip()
        if not prize_text:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': "❌ Iltimos, yutuq nomini matn shaklida yozing!"
            })
            return

        prize_html = entities_to_html(message.get('text', ''), message.get('entities', []))

        self.db.set_state(user_id, 'awaiting_create_battle_post', {'prize': prize_html})

        msg = f"✅ <b>Yutuq saqlandi:</b> {prize_html}\n\n"
        msg += "Endi, batl o'tkazmoqchi bo'lgan kanalingizdagi postni <b>forward</b> qilib yuboring yoki kanalga yubormoqchi bo'lgan xabaringizni jo'nating.\n\n"
        msg += "⚠️ <i>Xabar matnida yoki tagida <b>#boshlash</b> so'zi bo'lishi shart!</i>\n\n"
        msg += "❌ Bekor qilish uchun /cancel ni bosing."

        keyboard = {
            'inline_keyboard': [
                [{'text': "❌ Bekor qilish", 'callback_data': "back_to_main"}]
            ]
        }

        self.telegram_api('sendMessage', {
            'chat_id': chat_id,
            'text': msg,
            'parse_mode': 'HTML',
            'reply_markup': json.dumps(keyboard)
        })

    def process_create_battle_post_input(self, chat_id, user_id, message):
        user_state = self.db.get_state(user_id)
        prize = "Sir 🤫"

        if user_state and user_state.get('temp_data_decoded'):
            prize = user_state['temp_data_decoded'].get('prize', 'Sir 🤫')
        elif user_state and user_state.get('temp_data'):
            if isinstance(user_state['temp_data'], dict):
                prize = user_state['temp_data'].get('prize', 'Sir 🤫')
            else:
                try:
                    td = json.loads(user_state['temp_data'])
                    prize = td.get('prize', 'Sir 🤫')
                except Exception:
                    pass

        self.db.clear_state(user_id)
        
        text = message.get('text') or message.get('caption') or ''
        if '#boshlash' not in text.lower():
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': "❌ <b>Xatolik:</b> Yuborgan xabaringizda <code>#boshlash</code> so'zi bo'lishi shart!\n\nIltimos, qaytadan urinib ko'ring yoki /cancel bosing.",
                'parse_mode': 'HTML'
            })
            self.db.set_state(user_id, 'awaiting_create_battle_post', {'prize': prize})
            return

        target_channel_id = None
        if 'forward_from_chat' in message:
            target_channel_id = message['forward_from_chat']['id']
        else:
            channels = self.db.get_all_channels()
            admin_channels = [c for c in channels if self.is_channel_admin(user_id, c.get('channel_id'))]
            if admin_channels:
                target_channel_id = admin_channels[0]['channel_id']

        if not target_channel_id:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': "❌ <b>Kanal aniqlanmadi!</b>\n\nIltimos, batl o'tkazmoqchi bo'lgan kanalingizdagi postni to'g'ridan-to'g'ri forward qiling yoki botni kanalingizga admin qilganingizga ishonch hosil qiling.",
                'parse_mode': 'HTML'
            })
            self.send_main_menu(chat_id)
            return

        sent_msg = self.telegram_api('copyMessage', {
            'chat_id': target_channel_id,
            'from_chat_id': chat_id,
            'message_id': message['message_id']
        })

        if not sent_msg or not sent_msg.get('ok'):
            sent_msg = self.telegram_api('sendMessage', {
                'chat_id': target_channel_id,
                'text': text,
                'parse_mode': 'HTML'
            })

        if sent_msg and sent_msg.get('ok'):
            post_msg_id = sent_msg['result']['message_id']
            contest_id = self.db.create_contest(target_channel_id, post_msg_id, prize=prize)

            join_url = f"https://t.me/{self.bot_username}?start=join_{contest_id}"

            post_text = "<blockquote>🏆 <b>BATL Boshlandi🥳</b>\n\n"
            post_text += f"🎁 <b>Yutuq:</b> {prize}\n\n"
            post_text += "❗ Konkurs shartlari shu kanalga obuna bo'lish va do'stlaringiz sizga ovoz berishini so'rashdan iborat. Agar kanalga qo'shilib ovoz berib chiqib ketsa ovozi avtomatik olib tashlanadi ⛔\n\n"
            post_text += "➕ Konkursga qo'shilish uchun quyidagi tugmani bosing 👇</blockquote>"

            keyboard = {
                'inline_keyboard': [
                    [{'text': "🟢 Konkursga Qo'shilish ➕", 'url': join_url}],
                    [{'text': "🔴 Natijalar 📊", 'callback_data': f"results_{contest_id}"}]
                ]
            }

            self.telegram_api('editMessageText', {
                'chat_id': target_channel_id,
                'message_id': post_msg_id,
                'text': post_text,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })

            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': "✅ <b>Batl muvaffaqiyatli yaratildi va kanalga joylandi!</b> 🎉",
                'parse_mode': 'HTML'
            })
        else:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': "❌ <b>Xatolik:</b> Xabarni kanalga yuborib bo'lmadi. Bot o'sha kanalda Admin ekanligini tekshiring!",
                'parse_mode': 'HTML'
            })

        self.send_main_menu(chat_id)

    def send_top_channels(self, chat_id, edit_message_id=None):
        top_channels = self.db.get_top_channels(10)
        txt = "🏆 <b>TOP 10 KANAL</b> 🏆\n\n"

        if not top_channels:
            txt += "<i>Hozircha faol kanallar mavjud emas.</i>"
        else:
            medals = ["🥇", "🥈", "🥉"]
            for i, ch in enumerate(top_channels):
                c_name = ch['title']
                c_username = f"@{ch['username'].lstrip('@')}" if ch.get('username') else c_name
                total_contests = int(ch.get('total_contests', 0))
                active_contests = int(ch.get('active_contests', 0))
                total_participants = int(ch.get('total_participants', 0))
                total_votes = int(ch.get('total_votes', 0))

                icon = medals[i] if i < len(medals) else f"{i + 1}."

                txt += f"{icon} <b>{c_username}</b>\n"
                txt += f"🏆 Konkurslar: {total_contests} ta | Aktiv: {active_contests} ta\n"
                txt += f"👥 Qatnashchilar: {total_participants} ta | 📦 Ovozlar: {total_votes} ta\n\n"

            txt += "<i>Reyting konkurslar, qatnashchilar va ovozlar bo'yicha hisoblanadi.</i>"

        keyboard = {
            'inline_keyboard': [
                [{'text': "🔙 Orqaga", 'callback_data': "back_to_main"}]
            ]
        }

        if edit_message_id:
            self.telegram_api('editMessageText', {
                'chat_id': chat_id,
                'message_id': edit_message_id,
                'text': txt,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })
        else:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': txt,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })

    def send_contest_details(self, chat_id, contest_id, edit_message_id=None):
        contest = self.db.get_contest_by_id(contest_id)
        if not contest:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': "❌ Konkurs topilmadi.",
                'parse_mode': 'HTML'
            })
            return

        participants = self.db.get_participants_by_contest(contest_id)
        ch_username = f"@{contest['channel_username'].lstrip('@')}" if contest.get('channel_username') else contest.get('channel_title', '')
        
        created_at_dt = contest.get('created_at')
        created_at = created_at_dt.strftime('%Y-%m-%dT%H:%M') if isinstance(created_at_dt, datetime) else datetime.utcnow().strftime('%Y-%m-%dT%H:%M')
        status_str = "✅ Aktiv" if contest.get('status') == 'active' else "🔴 Yakunlangan"
        prize_str = contest.get('prize', 'Sir 🤫')

        txt = "🏆 <b>KONKURS MA'LUMOTLARI</b>\n\n"
        txt += f"Kanal: {ch_username}\n"
        txt += f"Yutuq: {prize_str}\n"
        txt += f"Yaratilgan: {created_at}\n"
        txt += f"Status: {status_str}\n\n"

        p_count = len(participants)
        txt += f"👥 <b>QATNASHCHILAR ({p_count} ta):</b>\n\n"

        if not participants:
            txt += "<i>Hozircha ishtirokchilar yo'q.</i>"
        else:
            for i, p in enumerate(participants):
                p_name = p['user_name']
                votes_count = int(p['votes_count'])
                icon = "🏅" if i < 3 else f"{i + 1}."

                txt += f"{icon} <b>{p_name}</b> — {votes_count} ovoz 🗳️\n"

                voters = self.db.get_votes_by_participant(p['id'])
                if voters:
                    voter_list = []
                    for v in voters:
                        if v.get('voter_username'):
                            voter_list.append(f"@{v['voter_username'].lstrip('@')}")
                        elif v.get('voter_name'):
                            voter_list.append(v['voter_name'])
                        else:
                            voter_list.append(f"Ishtirokchi ({v['voter_id']})")
                    txt += "   Ovoz berganlar: " + ", ".join(voter_list) + "\n\n"
                else:
                    txt += "   Ovoz berganlar: —\n\n"

        keyboard = {
            'inline_keyboard': [
                [{'text': "🔙 Asosiy Menyu", 'callback_data': "back_to_main"}]
            ]
        }

        if edit_message_id:
            self.telegram_api('editMessageText', {
                'chat_id': chat_id,
                'message_id': edit_message_id,
                'text': txt.strip(),
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })
        else:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': txt.strip(),
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })

    def prompt_join_contest(self, chat_id, user_id, contest_id, user_obj):
        if contest_id <= 0:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': "❌ Noto'g'ri konkurs havolasi.",
                'parse_mode': 'HTML'
            })
            return

        contest = self.db.get_contest_by_id(contest_id)
        if not contest:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': "❌ Ushbu konkurs ma'lumotlar bazasidan topilmadi.",
                'parse_mode': 'HTML'
            })
            return

        if contest.get('status') != 'active':
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': "❌ Ushbu konkurs allaqachon yakunlangan.",
                'parse_mode': 'HTML'
            })
            return

        if contest.get('channel_id'):
            if not self.check_user_subscribed(user_id, contest['channel_id']):
                ch_title = contest.get('channel_title', 'Kanal')
                ch_uname = f"@{contest['channel_username'].lstrip('@')}" if contest.get('channel_username') else None
                ch_link = f"https://t.me/{ch_uname.lstrip('@')}" if ch_uname else "#"

                msg = f"⚠️ <b>Konkursda qatnashish uchun avval «{ch_title}» kanaliga obuna bo'lishingiz kerak!</b>"
                keyboard = {
                    'inline_keyboard': [
                        [{'text': "📢 Kanalga obuna bo'lish 🟢", 'url': ch_link}],
                        [{'text': "✅ Obunani tekshirish 🔄", 'url': f"https://t.me/{self.bot_username}?start=join_{contest_id}"}]
                    ]
                }
                self.telegram_api('sendMessage', {
                    'chat_id': chat_id,
                    'text': msg,
                    'parse_mode': 'HTML',
                    'reply_markup': json.dumps(keyboard)
                })
                return

        existing = self.db.get_participant(contest_id, user_id)
        if existing:
            share_url = f"https://t.me/{self.bot_username}?start=vote_{contest_id}_{existing['id']}"
            txt = "❌ <b>Siz allaqachon ishtirokchisiz!</b>\n\n"
            txt += f"📊 Sizdagi ovozlar soni: <b>{existing['votes_count']} ta 🗳️</b>\n\n"
            txt += f"🔗 <b>Ovoz yig'ish uchun shaxsiy havolangiz:</b>\n<code>{share_url}</code>\n\n"
            txt += "Ushbu linkni do'stlaringizga tarqating!"

            keyboard = {
                'inline_keyboard': [
                    [{'text': "🚀 Linkni Ulashish 📲", 'url': f"https://t.me/share/url?url={requests.utils.quote(share_url)}&text={requests.utils.quote('Menga ovoz bering! 🏆')}"}]
                ]
            }

            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': txt,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })
            return

        user_name = f"@{user_obj['username'].lstrip('@')}" if user_obj.get('username') else user_obj.get('first_name', 'Ishtirokchi')
        if not user_obj.get('username') and user_obj.get('last_name'):
            user_name += f" {user_obj['last_name']}"

        participant_id = self.db.add_participant(contest_id, user_id, user_name, 'text', None, None)
        self.db.clear_state(user_id)

        self.update_contest_post(contest_id)

        share_url = f"https://t.me/{self.bot_username}?start=vote_{contest_id}_{participant_id}"

        txt = "✅ <b>Konkursga qo'shildingiz!</b>\n\n"
        txt += f"🔗 <b>Sizning shaxsiy ovoz yig'ish havolangiz:</b>\n<code>{share_url}</code>\n\n"
        txt += "Ushbu havolani do'stlaringizga yuboring va ovoz yig'ing!"

        keyboard = {
            'inline_keyboard': [
                [{'text': "🚀 Linkni Ulashish 📲", 'url': f"https://t.me/share/url?url={requests.utils.quote(share_url)}&text={requests.utils.quote('Menga ovoz bering! 🏆')}"}]
            ]
        }

        self.telegram_api('sendMessage', {
            'chat_id': chat_id,
            'text': txt,
            'parse_mode': 'HTML',
            'reply_markup': json.dumps(keyboard)
        })

    def process_participant_submission(self, message, contest_id):
        chat_id = message['chat']['id']
        user_id = message['from']['id']
        user_obj = message['from']

        user_name = f"@{user_obj['username'].lstrip('@')}" if user_obj.get('username') else user_obj.get('first_name', 'Ishtirokchi')
        if not user_obj.get('username') and user_obj.get('last_name'):
            user_name += f" {user_obj['last_name']}"

        media_type = 'text'
        file_id = None
        caption = message.get('caption')

        if 'photo' in message:
            media_type = 'photo'
            file_id = message['photo'][-1]['file_id']
        elif 'audio' in message:
            media_type = 'audio'
            file_id = message['audio']['file_id']
        elif 'voice' in message:
            media_type = 'voice'
            file_id = message['voice']['file_id']

        participant_id = self.db.add_participant(contest_id, user_id, user_name, media_type, file_id, caption)
        self.db.clear_state(user_id)

        self.update_contest_post(contest_id)

        share_url = f"https://t.me/{self.bot_username}?start=vote_{contest_id}_{participant_id}"

        txt = "🎉 <b>Tabriklaymiz! Siz konkursga muvaffaqiyatli qo'shildingiz!</b>\n\n"
        txt += f"🔗 <b>Sizning shaxsiy ovoz yig'ish havolangiz:</b>\n<code>{share_url}</code>\n\n"
        txt += "Ushbu havolani do'stlaringizga yuboring va ovoz yig'ing!"

        keyboard = {
            'inline_keyboard': [
                [{'text': "🚀 Linkni Ulashish 📲", 'url': f"https://t.me/share/url?url={requests.utils.quote(share_url)}&text={requests.utils.quote('Menga ovoz bering! 🏆')}"}]
            ]
        }

        self.telegram_api('sendMessage', {
            'chat_id': chat_id,
            'text': txt,
            'parse_mode': 'HTML',
            'reply_markup': json.dumps(keyboard)
        })

    def process_vote(self, chat_id, user_id, contest_id, participant_id, user_obj=None):
        contest = self.db.get_contest_by_id(contest_id)
        if not contest:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': "❌ Konkurs topilmadi.",
                'parse_mode': 'HTML'
            })
            return

        if contest.get('status') != 'active':
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': "🔴 <b>Ushbu konkurs allaqachon yakunlangan! Ovoz berish to'xtatilgan.</b>",
                'parse_mode': 'HTML'
            })
            return

        participant = self.db.get_participant_by_id(participant_id)
        if not participant:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': "❌ Ishtirokchi topilmadi.",
                'parse_mode': 'HTML'
            })
            return

        if str(participant.get('user_id')) == str(user_id):
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': "❌ <b>O'zingizga ovoz bera olmaysiz!</b>",
                'parse_mode': 'HTML'
            })
            return

        if contest.get('channel_id'):
            if not self.check_user_subscribed(user_id, contest['channel_id']):
                ch_title = contest.get('channel_title', 'Kanal')
                ch_uname = f"@{contest['channel_username'].lstrip('@')}" if contest.get('channel_username') else None
                ch_link = f"https://t.me/{ch_uname.lstrip('@')}" if ch_uname else "#"

                msg = f"⚠️ <b>Ovoz berish uchun avval «{ch_title}» kanaliga obuna bo'lishingiz kerak!</b>"
                keyboard = {
                    'inline_keyboard': [
                        [{'text': "📢 Kanalga obuna bo'lish 🟢", 'url': ch_link}],
                        [{'text': "✅ Obunani tekshirish & Ovoz berish 🗳️", 'url': f"https://t.me/{self.bot_username}?start=vote_{contest_id}_{participant_id}"}]
                    ]
                }
                self.telegram_api('sendMessage', {
                    'chat_id': chat_id,
                    'text': msg,
                    'parse_mode': 'HTML',
                    'reply_markup': json.dumps(keyboard)
                })
                return

        voter_username = user_obj.get('username') if user_obj else None
        voter_name = user_obj.get('first_name', 'Ishtirokchi') if user_obj else 'Ishtirokchi'
        if user_obj and not user_obj.get('username') and user_obj.get('last_name'):
            voter_name += ' ' + user_obj['last_name']

        success = self.db.add_vote(contest_id, participant_id, user_id, voter_username, voter_name)

        if success:
            self.update_contest_post(contest_id)
            p_name = participant['user_name']
            txt = f"🎉 <b>Ovozingiz «{p_name}» uchun muvaffaqiyatli qabul qilindi! 🗳️</b>"
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': txt,
                'parse_mode': 'HTML'
            })
            self.send_contest_details(chat_id, contest_id)
        else:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': "⚠️ <b>Siz ushbu konkursda allaqachon ovoz bergansiz!</b>",
                'parse_mode': 'HTML'
            })

    def update_contest_post(self, contest_id):
        """
        Qatnashuvchilar qo'shilganda yoki ovozlar o'zgarganda xabarni yangilaydi.
        Kiritilgan yutuq (va uning barcha premium emojilari) bazadan olinadi va hech qachon yo'qolmaydi!
        """
        try:
            contest = self.db.get_contest_by_id(contest_id)
            if not contest or not contest.get('channel_id') or not contest.get('post_message_id'):
                return

            participants = self.db.get_contest_participants(contest_id)
            join_url = f"https://t.me/{self.bot_username}?start=join_{contest_id}"
            prize = contest.get('prize', 'Sir 🤫')

            keyboard = {'inline_keyboard': []}

            # Qatnashuvchilar tugmalari
            for p in participants:
                p_name = p.get('user_name') or 'Ishtirokchi'
                vote_url = f"https://t.me/{self.bot_username}?start=vote_{contest_id}_{p['id']}"
                btn_text = f"👤 {p_name} — {p['votes_count']} 🗳️"
                keyboard['inline_keyboard'].append([
                    {'text': btn_text, 'url': vote_url}
                ])

            # Qo'shilish va Natijalar tugmalari
            keyboard['inline_keyboard'].append([
                {'text': "🟢 Konkursga Qo'shilish ➕", 'url': join_url}
            ])
            keyboard['inline_keyboard'].append([
                {'text': "🔴 Natijalar 📊", 'callback_data': f"results_{contest_id}"}
            ])

            # Post matnida yutuq har doim saqlanib turadi
            post_text = "<blockquote>🏆 <b>BATL Boshlandi🥳</b>\n\n"
            post_text += f"🎁 <b>Yutuq:</b> {prize}\n\n"
            post_text += "❗ Konkurs shartlari shu kanalga obuna bo'lish va do'stlaringiz sizga ovoz berishini so'rashdan iborat. Agar kanalga qo'shilib ovoz berib chiqib ketsa ovozi avtomatik olib tashlanadi ⛔\n\n"
            post_text += "➕ Konkursga qo'shilish uchun quyidagi tugmani bosing 👇</blockquote>"

            self.telegram_api('editMessageText', {
                'chat_id': contest['channel_id'],
                'message_id': contest['post_message_id'],
                'text': post_text,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })
        except Exception as e:
            logging.error(f"Error in update_contest_post: {e}", exc_info=True)

    def process_check_channel_input(self, chat_id, user_id, message):
        self.db.clear_state(user_id)
        contest = None

        if 'forward_from_chat' in message:
            f_channel_id = message['forward_from_chat']['id']
            if 'forward_from_message_id' in message:
                f_msg_id = message['forward_from_message_id']
                contest = self.db.get_contest_by_post_message(f_channel_id, f_msg_id)

            if not contest:
                contest = self.db.get_active_contest_by_channel(f_channel_id)

        if contest:
            self.send_contest_details(chat_id, contest['id'])
        else:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': "❌ Bu post uchun konkurs topilmadi. Faqat #boshlash bilan yaratilgan konkurs postlari tekshiriladi.",
                'parse_mode': 'HTML'
            })

    def send_admin_panel(self, chat_id, edit_message_id=None):
        txt = "👑 <b>BOT ADMIN PANEL</b>\n\nKerakli bo'limni tanlang:"
        keyboard = {
            'inline_keyboard': [
                [
                    {'text': "📊 Bot Statistikasi 📈", 'callback_data': "admin_stats"},
                    {'text': "📢 Majburiy Kanallar ⚙️", 'callback_data': "admin_channels_menu"}
                ],
                [
                    {'text': "📣 Kanallarga Xabar 🚀", 'callback_data': "admin_broadcast_channels"},
                    {'text': "🔙 Asosiy Menyu 🏠", 'callback_data': "back_to_main"}
                ]
            ]
        }

        if edit_message_id:
            self.telegram_api('editMessageText', {
                'chat_id': chat_id,
                'message_id': edit_message_id,
                'text': txt,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })
        else:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': txt,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })

    def send_admin_channels_menu(self, chat_id, edit_message_id=None):
        mand_channels = self.db.get_mandatory_channels()

        txt = "📢 <b>MAJBURIY KANALLAR BO'LIMI</b>\n\n"
        txt += f"Hozirda <b>{len(mand_channels)} ta</b> majburiy obuna kanali sozlangay.\n\n"
        txt += "Kerakli bo'limni tanlang:"

        keyboard = {
            'inline_keyboard': [
                [{'text': "📋 Kanallar Ro'yxati", 'callback_data': "admin_list_channels"}],
                [
                    {'text': "➕ Kanal qo'shish 🟢", 'callback_data': "admin_add_channel"},
                    {'text': "🗑 Kanal o'chirish 🔴", 'callback_data': "admin_del_channel"}
                ],
                [{'text': "⬅️ Admin Panelga Qaytish 👑", 'callback_data': "admin_panel"}]
            ]
        }

        if edit_message_id:
            self.telegram_api('editMessageText', {
                'chat_id': chat_id,
                'message_id': edit_message_id,
                'text': txt,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })
        else:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': txt,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })

    def send_admin_list_channels(self, chat_id, edit_message_id=None):
        mand_channels = self.db.get_mandatory_channels()
        txt = f"📋 <b>MAJBURIY KANALLAR RO'YXATI</b> ({len(mand_channels)} ta)\n\n"

        if not mand_channels:
            txt += "<i>Hozircha majburiy obuna kanallari yo'q.</i>\n\n"
        else:
            for i, ch in enumerate(mand_channels, start=1):
                uname = f"@{ch['username'].lstrip('@')}" if ch.get('username') else "Shaxsiy havola"
                txt += f"{i}. 📢 <b>{ch['title']}</b> ({uname})\n"
            txt += "\n"

        keyboard = {
            'inline_keyboard': [
                [
                    {'text': "➕ Kanal qo'shish 🟢", 'callback_data': "admin_add_channel"},
                    {'text': "🗑 Kanal o'chirish 🔴", 'callback_data': "admin_del_channel"}
                ],
                [{'text': "⬅️ Kanallar Bo'limiga Qaytish 📢", 'callback_data': "admin_channels_menu"}]
            ]
        }

        if edit_message_id:
            self.telegram_api('editMessageText', {
                'chat_id': chat_id,
                'message_id': edit_message_id,
                'text': txt,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })
        else:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': txt,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })

    def send_admin_stats(self, chat_id, edit_message_id=None):
        stats = self.db.get_stats()

        txt = "📊 <b>BOT STATISTIKASI</b> 📊\n\n"
        txt += f"📢 <b>Ulangan kanallar:</b> {stats['total_channels']} ta\n"
        txt += f"🏆 <b>Jami Batllar:</b> {stats['total_contests']} ta\n"
        txt += f"  ├ 🟢 Aktiv: {stats['active_contests']} ta\n"
        txt += f"  └ 🔴 Yakunlangan: {stats['ended_contests']} ta\n\n"
        txt += f"👥 <b>Jami Qatnashchilar:</b> {stats['total_participants']} ta\n"
        txt += f"🗳 <b>Jami Ovozlar:</b> {stats['total_votes']} ta\n"
        txt += f"👤 <b>Unikal Ovoz Beruvchilar:</b> {stats['unique_voters']} ta\n"
        txt += f"📢 <b>Majburiy Obuna Kanallari:</b> {stats['mandatory_channels']} ta"

        keyboard = {
            'inline_keyboard': [
                [{'text': "⬅️ Admin panelga qaytish 👑", 'callback_data': "admin_panel"}]
            ]
        }

        if edit_message_id:
            self.telegram_api('editMessageText', {
                'chat_id': chat_id,
                'message_id': edit_message_id,
                'text': txt,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })
        else:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': txt,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })

    def send_admin_delete_channel_menu(self, chat_id, edit_message_id=None):
        mand_channels = self.db.get_mandatory_channels()

        txt = "🗑 <b>MAJBURIY KANALNI O'CHIRISH</b>\n\n"
        if not mand_channels:
            txt += "O'chirish uchun kanallar mavjud emas."
            keyboard = {
                'inline_keyboard': [
                    [{'text': "⬅️ Kanallar Bo'limiga Qaytish 📢", 'callback_data': "admin_channels_menu"}]
                ]
            }
        else:
            txt += "O'chirmoqchi bo'lgan kanalingiz ustiga bosing:"
            keyboard = {'inline_keyboard': []}
            for ch in mand_channels:
                keyboard['inline_keyboard'].append([
                    {'text': f"❌ {ch['title']}", 'callback_data': f"del_mand_{ch['id']}"}
                ])
            keyboard['inline_keyboard'].append([
                {'text': "⬅️ Kanallar Bo'limiga Qaytish 📢", 'callback_data': "admin_channels_menu"}
            ])

        if edit_message_id:
            self.telegram_api('editMessageText', {
                'chat_id': chat_id,
                'message_id': edit_message_id,
                'text': txt,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })
        else:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': txt,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })

    def process_add_mandatory_channel_input(self, chat_id, user_id, message):
        channel_id = None
        title = None
        username = None

        if 'forward_from_chat' in message:
            channel_id = message['forward_from_chat']['id']
            title = message['forward_from_chat']['title']
            username = message['forward_from_chat'].get('username')
        else:
            input_text = message.get('text', '').strip()
            if not input_text:
                self.telegram_api('sendMessage', {
                    'chat_id': chat_id,
                    'text': "❌ Noto'g'ri kiritish. Iltimos kanal postini forward qiling yoki username yuboring.",
                    'parse_mode': 'HTML'
                })
                return

            clean_username = input_text.lstrip('@')
            if 't.me/' in clean_username:
                clean_username = clean_username.split('t.me/')[1].split('/')[0].strip()

            chat_info = self.telegram_api('getChat', {'chat_id': f"@{clean_username}"})
            if chat_info and chat_info.get('ok') and chat_info.get('result'):
                channel_id = chat_info['result']['id']
                title = chat_info['result']['title']
                username = chat_info['result'].get('username', clean_username)
            else:
                self.telegram_api('sendMessage', {
                    'chat_id': chat_id,
                    'text': "❌ Kanal topilmadi. Bot ushbu kanalda admin qilinganiga va username to'g'riligiga ishonch hosil qiling.",
                    'parse_mode': 'HTML'
                })
                return

        self.db.add_mandatory_channel(channel_id, title, username)
        self.db.clear_state(user_id)

        self.telegram_api('sendMessage', {
            'chat_id': chat_id,
            'text': f"✅ <b>{title}</b> kanali majburiy obuna kanallar ro'yxatiga muvaffaqiyatli qo'shildi!",
            'parse_mode': 'HTML'
        })

        self.send_admin_channels_menu(chat_id)

    def process_channel_broadcast_input(self, chat_id, user_id, message):
        self.db.clear_state(user_id)

        status_msg = self.telegram_api('sendMessage', {
            'chat_id': chat_id,
            'text': "⏳ <b>Xabaringiz kanallarga tarqatilmoqda...</b>\n\n<i>Iltimos, jarayon yakunlanishini kuting.</i>",
            'parse_mode': 'HTML'
        })

        status_msg_id = status_msg.get('result', {}).get('message_id') if status_msg else None

        channels = self.db.get_all_channels()
        mand_channels = self.db.get_mandatory_channels()

        target_channel_ids = set()
        for c in channels:
            if c.get('channel_id'):
                target_channel_ids.add(str(c['channel_id']))
        for mc in mand_channels:
            if mc.get('channel_id'):
                target_channel_ids.add(str(mc['channel_id']))

        if not target_channel_ids:
            no_chan_text = "⚠️ <b>Hozircha bot qo'shilgan yoki ro'yxatdan o'tgan hech qanday kanal topilmadi.</b>\n\nKanalda <code>#boshlash</code> posti joylanganda yoki Majburiy Kanallar bo'limida kanal qo'shilganda bot avtomatik kanallarni ro'yxatga oladi."
            if status_msg_id:
                self.telegram_api('editMessageText', {
                    'chat_id': chat_id,
                    'message_id': status_msg_id,
                    'text': no_chan_text,
                    'parse_mode': 'HTML'
                })
            else:
                self.telegram_api('sendMessage', {
                    'chat_id': chat_id,
                    'text': no_chan_text,
                    'parse_mode': 'HTML'
                })
            self.send_admin_panel(chat_id)
            return

        success_count = 0
        fail_count = 0

        for ch_id in target_channel_ids:
            res = self.telegram_api('copyMessage', {
                'chat_id': ch_id,
                'from_chat_id': chat_id,
                'message_id': message['message_id']
            })

            if (not res or not res.get('ok')) and message.get('text'):
                res = self.telegram_api('sendMessage', {
                    'chat_id': ch_id,
                    'text': message['text'],
                    'parse_mode': 'HTML'
                })

            if res and res.get('ok'):
                success_count += 1
            else:
                fail_count += 1
                desc = res.get('description', '') if res else ''
                if any(err in desc for err in ['kicked', 'not a member', 'not found', 'Forbidden']):
                    self.db.remove_channel(ch_id)

        report_text = "📣 <b>KANALLARGA XABAR TARQATILDI!</b>\n\n"
        report_text += f"🟢 Muvaffaqiyatli jo'natildi: <b>{success_count}</b> ta kanal\n"
        report_text += f"🔴 Xatolik: <b>{fail_count}</b> ta kanal"

        if status_msg_id:
            self.telegram_api('editMessageText', {
                'chat_id': chat_id,
                'message_id': status_msg_id,
                'text': report_text,
                'parse_mode': 'HTML'
            })
        else:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': report_text,
                'parse_mode': 'HTML'
            })

        self.send_admin_panel(chat_id)

    def check_user_subscribed(self, user_id, channel_id):
        if not channel_id:
            return True

        res = self.telegram_api('getChatMember', {
            'chat_id': channel_id,
            'user_id': user_id
        })

        if res and res.get('ok') and res.get('result', {}).get('status'):
            return res['result']['status'] in ['creator', 'administrator', 'member']

        return False

    def is_channel_admin(self, user_id, channel_id):
        if self.is_admin(user_id):
            return True

        if not channel_id:
            return False

        res = self.telegram_api('getChatMember', {
            'chat_id': channel_id,
            'user_id': user_id
        })

        if res and res.get('ok') and res.get('result', {}).get('status'):
            return res['result']['status'] in ['creator', 'administrator']

        return False

    def check_global_mandatory_subscription(self, user_id):
        if self.is_admin(user_id):
            return []

        mand_channels = self.db.get_mandatory_channels()
        if not mand_channels:
            return []

        unsubscribed = []
        for ch in mand_channels:
            if not self.check_user_subscribed(user_id, ch['channel_id']):
                unsubscribed.append(ch)

        return unsubscribed

    def prompt_mandatory_subscription(self, chat_id, unsubscribed_channels, edit_message_id=None):
        txt = "⚠️ <b>BOTDAN FOYDALANISH UCHUN QUYIDAGI KANALLARGA OBUNA BO'LING!</b>\n\n"
        txt += "Botdan foydalanishni davom ettirish uchun barcha majburiy obuna kanallariga a'zo bo'ling va «✅ Obunani tekshirish» tugmasini bosing:\n\n"

        keyboard = {'inline_keyboard': []}

        for i, ch in enumerate(unsubscribed_channels, start=1):
            c_name = ch['title']
            txt += f"{i}. 📢 <b>{c_name}</b>\n"

            url = "#"
            if ch.get('username'):
                url = f"https://t.me/{ch['username'].lstrip('@')}"
            elif ch.get('invite_link'):
                url = ch['invite_link']

            keyboard['inline_keyboard'].append([
                {'text': "📢 " + c_name, 'url': url}
            ])

        keyboard['inline_keyboard'].append([
            {'text': "✅ Obunani tekshirish 🔄", 'callback_data': "check_global_sub"}
        ])

        if edit_message_id:
            self.telegram_api('editMessageText', {
                'chat_id': chat_id,
                'message_id': edit_message_id,
                'text': txt,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })
        else:
            self.telegram_api('sendMessage', {
                'chat_id': chat_id,
                'text': txt,
                'parse_mode': 'HTML',
                'reply_markup': json.dumps(keyboard)
            })

    def is_admin(self, user_id):
        admin_id = str(self.config.get('admin_id', ''))
        return str(user_id) == admin_id

# ==================== FLASK ROUTES & LONG POLLING ====================
bot_instance = KonkursBot()

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "online",
        "bot_username": bot_instance.bot_username,
        "message": "Konkurs Boti serverda muvaffaqiyatli ishlamoqda!"
    }), 200

@app.route('/', methods=['POST'])
def webhook():
    if request.is_json:
        update = request.get_json()
        if update:
            bot_instance.handle_update(update)
    return jsonify({"status": "ok"}), 200

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    url = request.args.get('url')
    if not url:
        return jsonify({"error": "Iltimos ?url=https://yourdomain.com parametringizni kiriting"}), 400
    res = telegram_api('setWebhook', {'url': url})
    return jsonify(res), 200

def start_long_polling():
    logging.info("🚀 Long Polling rejimida Telegram'dan so'rovlar olinmoqda...")
    telegram_api('deleteWebhook', {'drop_pending_updates': False})
    
    offset = 0
    while True:
        try:
            updates = telegram_api('getUpdates', {'offset': offset, 'timeout': 20})
            if updates and updates.get('ok') and updates.get('result'):
                for update in updates['result']:
                    offset = update['update_id'] + 1
                    bot_instance.handle_update(update)
        except Exception as e:
            logging.error(f"Polling loop Error: {e}")
            time.sleep(3)

if __name__ == '__main__':
    polling_thread = threading.Thread(target=start_long_polling, daemon=True)
    polling_thread.start()
    
    app.run(host='0.0.0.0', port=5000, debug=False)
