import logging
import asyncio
import sys
import io
import re
import html
from datetime import datetime
from typing import Optional
from urllib.parse import quote_plus
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, CopyTextButton
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from database import Database, ADMIN_PERMISSION_DEFINITIONS
from config import (
    BOT_TOKEN, ADMIN_IDS, BOT_NAME, BOT_USERNAME,
    SUPPORT_USERNAME, MIN_WITHDRAW_AMOUNT
)

# Windows কনসোলে বাংলা ও ইউনিকোড সঠিক প্রদর্শনের জন্য এনকোডিং ফিক্স
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

db = Database()

DEFAULT_BOT_DESCRIPTION = """🎓 StudyMart-এ আপনাকে স্বাগতম!

আপনার বিশ্বস্ত অটোমেটেড কোর্স প্ল্যাটফর্ম।

📖 SSC • 📚 HSC • 🎯 Admission • 💼 Skills

💳 নিরাপদ পেমেন্ট
⚡ তাত্ক্ষণিক কোর্স অ্যাক্সেস
🤖 সম্পূর্ণ অটোমেটেড"""


def is_admin(user_id: int) -> bool:
    if not user_id:
        return False
    return db.is_admin(user_id)


def format_order_id_display(oid: str) -> str:
    if not oid:
        return "N/A"
    oid_str = str(oid).strip()
    if oid_str.upper().startswith("ORD-"):
        return f"#{oid_str[4:]}"
    if not oid_str.startswith("#"):
        return f"#{oid_str}"
    return oid_str


def parse_inline_buttons(text: str) -> tuple[str, list[list[InlineKeyboardButton]]]:
    if not text:
        return "", []
    
    lines = text.split("\n")
    body_lines = []
    keyboard = []
    
    btn_pattern = re.compile(r"\[\s*([^\]|]+?)\s*\|\s*([^\]|]+?)\s*\]")
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            body_lines.append(line)
            continue
            
        remainder = btn_pattern.sub("", stripped).strip()
        if remainder == "":
            matches = btn_pattern.findall(stripped)
            row = []
            for btn_text, btn_target in matches:
                btn_text = btn_text.strip()
                btn_target = btn_target.strip()
                if btn_target.startswith("http://") or btn_target.startswith("https://") or btn_target.startswith("tg://"):
                    row.append(InlineKeyboardButton(btn_text, url=btn_target))
                else:
                    row.append(InlineKeyboardButton(btn_text, callback_data=btn_target))
            if row:
                keyboard.append(row)
        else:
            body_lines.append(line)
            
    body_text = "\n".join(body_lines).rstrip()
    return body_text, keyboard


async def wizard_edit_or_reply(context, update, text, parse_mode="Markdown", reply_markup=None):
    last_msg_id = context.user_data.get("last_wizard_msg_id")
    chat_id = (
        update.effective_chat.id if update.effective_chat 
        else (update.message.chat_id if update.message 
              else (update.callback_query.message.chat_id if update.callback_query and update.callback_query.message else None))
    )
    if last_msg_id and chat_id:
        try:
            # Remove inline buttons from the previous wizard step so they can't be clicked
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=last_msg_id,
                reply_markup=None
            )
        except Exception:
            pass

    if update.message:
        sent = await update.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    else:
        # Fallback if update is a callback query
        query = update.callback_query
        sent = await query.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        
    context.user_data["last_wizard_msg_id"] = sent.message_id


def check_maintenance(user_id: int) -> Tuple[bool, str]:
    if db.get_setting("maintenance_mode") == True and not is_admin(user_id):
        msg = db.get_setting("maintenance_message", "🛠️ **StudyMart Bot is currently undergoing scheduled maintenance. Please check back later.**")
        return True, msg
    return False, ""


async def get_dynamic_access_link(bot, access_link: str, user_id: int) -> Optional[str]:
    user_link = db.get_user_access_link(user_id, None)
    for course in db.get_all_courses().values():
        if course.get("access_link") == access_link:
            user_link = db.get_user_access_link(user_id, course.get("id"))
            break
    if user_link:
        return user_link
    if access_link and ("t.me/+" in access_link or "t.me/joinchat" in access_link):
        try:
            invite_link = await bot.create_chat_invite_link(
                chat_id=access_link.split("t.me/")[-1].replace("+", "").replace("joinchat", ""),
                member_limit=1,
                name=f"User {user_id}"
            )
            link = invite_link.invite_link
            for course in db.get_all_courses().values():
                if course.get("access_link") == access_link:
                    db.store_user_access_link(user_id, course.get("id"), link)
                    break
            return link
        except Exception:
            pass
    return access_link


def main_menu_keyboard(user_id: int):
    custom_kb = db.get_custom_keyboards()
    keyboard = []
    for row in custom_kb.get("buttons", []):
        row_buttons = []
        for btn in row:
            if btn.get("admin_only") and not is_admin(user_id):
                continue
            row_buttons.append(KeyboardButton(btn["text"]))
        if row_buttons:
            keyboard.append(row_buttons)
            
    if not keyboard:
        keyboard = [[KeyboardButton("🛍️ Cart"), KeyboardButton("👤 Profile"), KeyboardButton("ℹ Info")]]
        if is_admin(user_id):
            keyboard.append([KeyboardButton("⚙ Admin Panel")])
            
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True, one_time_keyboard=False)


WELCOME_MESSAGE = f"""আসসালামু আলাইকুম! 👋

🎓 **{BOT_NAME}**-এ আপনাকে স্বাগতম!

এখানে আপনি আপনার প্রয়োজনীয় যেকোনো একাডেমিক ও ভর্তি প্রস্তুতির কোর্স খুব সহজেই খুঁজে পাবেন, ক্রয় করতে পারবেন এবং তাৎক্ষণিকভাবে কোর্সের প্রিমিয়াম অ্যাক্সেস পেয়ে যাবেন।

✦ **আমাদের বিশেষ সুবিধাসমূহ:**
• ফুল এইচডি রেকর্ডেড ক্লাস ও নিয়মিত লাইভ সেশন
• সেরা মানের লেকচার শিট, হ্যান্ডনোট ও প্রশ্নব্যাংক
• যেকোনো কোর্সের নাম লিখলেই ইনস্ট্যান্ট সার্চ
• বিকাশ, নগদ ও রকেটের মাধ্যমে ইনস্ট্যান্ট পেমেন্ট
• ২৪/৭ নির্ভরযোগ্য ডেলিভারি ও এডমিন সাপোর্ট

"""


def get_home_keyboard_grid() -> list:
    grid = db.get_setting("home_keyboard_grid")
    
    # Force migration to add Browse E-Book if it is missing
    has_browse_eb = False
    if grid:
        for row in grid:
            for btn in row:
                if btn.get("action") == "browse_ebooks":
                    has_browse_eb = True
                    break
        if not has_browse_eb:
            grid = None
            
    if not grid:
        from config import SUPPORT_USERNAME
        grid = [
            [
                {"text": "📚 Browse Courses", "action": "browse_categories", "enabled": True}
            ],
            [
                {"text": "📖 Browse E-Book", "action": "browse_ebooks", "enabled": True}
            ],
            [
                {"text": "🎓 My Courses", "action": "my_courses_nav", "enabled": True},
                {"text": "📃 My E-Books", "action": "my_ebooks_nav", "enabled": True}
            ],
            [
                {"text": "💬 Support", "action": f"https://t.me/{SUPPORT_USERNAME}", "enabled": True}
            ]
        ]
        db.set_setting("home_keyboard_grid", grid)
    return grid


def get_welcome_inline_keyboard():
    grid = get_home_keyboard_grid()
    keyboard = []
    for row in grid:
        keyboard_row = []
        for btn in row:
            if btn.get("enabled", True):
                text = btn["text"]
                action = btn["action"]
                if action.startswith("http://") or action.startswith("https://") or action.startswith("tg://"):
                    keyboard_row.append(InlineKeyboardButton(text, url=action))
                else:
                    keyboard_row.append(InlineKeyboardButton(text, callback_data=action))
        if keyboard_row:
            keyboard.append(keyboard_row)
    return InlineKeyboardMarkup(keyboard)


# ==================== START & NAVIGATION ====================

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0

    # Capture contextual info before clearing
    active_dir = context.user_data.get("active_dir")
    active_eb_dir = context.user_data.get("active_eb_dir")
    origin_cb = context.user_data.get("course_origin_callback")

    # Clean up ALL active user_data wizard/input state keys
    state_keys = [
        "admin_user_step", "admin_order_search_step", "admin_broadcasting_step",
        "admin_broadcasting_mode", "bc_payload", "bc_target_mode", "bc_recipients",
        "withdraw_step", "awaiting_coupon", "coupon_course", "checkout_type",
        "awaiting_trxid", "pending_order_data",
        "admin_add_course", "course_step", "new_course", "course_origin_callback",
        "admin_add_ebook", "ebook_step", "new_ebook",
        "admin_edit_course_id", "admin_edit_course_field",
        "admin_edit_ebid", "admin_edit_ebook_field",
        "admin_pay_step", "admin_pay_target_key", "new_pay_method",
        "coupon_wizard_step", "new_coupon_data",
        "awaiting_folder_rename", "rename_folder_old", "rename_folder_parent",
        "awaiting_eb_folder_rename", "rename_eb_folder_old", "rename_eb_folder_parent",
        "admin_msg_edit_key", "edit_msg_key", "edit_msg_name",
        "admin_add_hbtn_step", "admin_add_hbtn_data", "admin_edit_hbtn_coords",
        "admin_add_kb_step", "admin_edit_kb_step", "admin_add_category",
        "admin_add_subcat", "admin_add_eb_subcat", "admin_edit_field",
        "active_dir", "active_eb_dir"
    ]
    for k in state_keys:
        context.user_data.pop(k, None)

    msg = """<blockquote>✕ <b>Action Cancelled / বাতিল করা হয়েছে</b></blockquote>

<blockquote>💡 নিচের বাটন চেপে আপনার প্রয়োজনীয় মেনুতে ফিরে যান:</blockquote>"""

    keyboard = []
    if is_admin(user_id):
        if origin_cb and origin_cb != "adm_main":
            keyboard.append([InlineKeyboardButton("« Return to Previous Menu", callback_data=origin_cb)])
        elif active_dir:
            keyboard.append([InlineKeyboardButton("« Return to Category", callback_data=f"adm_dir_{active_dir}")])
        elif active_eb_dir:
            keyboard.append([InlineKeyboardButton("« Return to E-Book Category", callback_data=f"adm_ebdir_{active_eb_dir}")])

        row_admin = [InlineKeyboardButton("👑 Admin Dashboard", callback_data="adm_main")]
        if db.is_home_button_enabled():
            row_admin.append(InlineKeyboardButton("⚜️ Main Menu", callback_data="back_to_main_menu"))
        keyboard.append(row_admin)
    else:
        row_user = []
        if db.is_home_button_enabled():
            row_user.append(InlineKeyboardButton("⚜️ Main Menu", callback_data="back_to_main_menu"))
        row_user.append(InlineKeyboardButton("📚 Browse Courses", callback_data="browse_categories"))
        keyboard.append(row_user)

    if update.callback_query:
        try:
            await update.callback_query.answer("Action cancelled.")
        except Exception:
            pass
        if update.callback_query.message.photo:
            try:
                await update.callback_query.message.delete()
            except Exception:
                pass
            await update.callback_query.message.reply_text(
                msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.callback_query.edit_message_text(
                msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
            )
    elif update.message:
        await update.message.reply_text(
            msg,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    under_maint, maint_msg = check_maintenance(user.id)
    if under_maint:
        await update.message.reply_text(maint_msg, parse_mode="Markdown")
        return
    referrer_id = None

    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referrer_id = int(arg.replace("ref_", ""))
            except ValueError:
                referrer_id = None
        elif arg.startswith("course_"):
            course_id = arg.replace("course_", "")
            db.add_user(user.id, user.username or "", user.full_name)
            course = db.get_course(course_id)
            if course:
                await send_course_details(update.message, context, course, user.id)
                return

    is_new, valid_ref = db.add_user(user.id, user.username or "", user.full_name, referrer_id=referrer_id)

    if is_new and valid_ref:
        try:
            student_name = user.full_name or f"@{user.username}" if user.username else f"User {user.id}"
            uname_tag = f" (@{user.username})" if user.username else ""
            reward_amt = db.get_referral_reward_amount()
            await context.bot.send_message(
                valid_ref,
                f"""🎉 <b>New Referral Joined!</b>
━━━━━━━━━━━━━━━━━━━━
👤 <b>Student:</b> {html.escape(student_name)}{uname_tag}
🆔 <b>User ID:</b> <code>{user.id}</code>

💡 <i>When this student purchases a paid course, a ৳{reward_amt} BDT referral bonus will be credited to your account.</i>""",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to send referral join notification: {e}")

    if not ADMIN_IDS and user.id not in ADMIN_IDS:
        ADMIN_IDS.append(user.id)
        logger.info(f"প্রাথমিক অ্যাডমিন যুক্ত করা হয়েছে: {user.id} (@{user.username})")

    logger.info(f"ইউজার স্টার্ট করেছে: {user.id} | @{user.username} | {user.full_name}")

    raw_welcome = db.get_setting("welcome_message", WELCOME_MESSAGE)
    cleaned_welcome, custom_kb = parse_inline_buttons(raw_welcome)
    
    # Always send persistent reply keyboard so bottom buttons are visible
    await update.message.reply_text(
        "👋 Welcome to StudyMart!",
        reply_markup=main_menu_keyboard(user.id)
    )

    if custom_kb:
        await update.message.reply_text(
            cleaned_welcome,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(custom_kb)
        )
    else:
        welcome_kb = get_welcome_inline_keyboard()
        await update.message.reply_text(
            raw_welcome,
            parse_mode="Markdown",
            reply_markup=welcome_kb
        )


# ==================== DYNAMIC CATEGORIES & SUB-CATEGORIES BROWSING ====================

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    show_inactive = is_admin(user_id)
    categories = db.get_categories(include_inactive=show_inactive)
    keyboard = []
    
    if not categories:
        text = "📂 **বর্তমানে কোনো ক্যাটাগরি উপলব্ধ নেই।**"
    else:
        text = "📂 **আপনার ব্যাচ/শ্রেণী নির্বাচন করুন:**"
        row = []
        for cat in categories:
            is_active = db.is_category_active(cat)
            tag = " [OFF 🔴]" if (not is_active and show_inactive) else ""
            row.append(InlineKeyboardButton(f"{cat}{tag}", callback_data=f"cat_{cat}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    if db.is_view_all_courses_enabled():
        all_courses = db.get_courses_by_filter(include_inactive=show_inactive)
        if all_courses:
            keyboard.append([InlineKeyboardButton(f"📚 Browse All Courses ({len(all_courses)})", callback_data="cat_ALL")])

    if db.is_home_button_enabled():
        keyboard.append([InlineKeyboardButton("⚜️ HOME", callback_data="back_to_main_menu")])

    if update.callback_query:
        if update.callback_query.message.photo:
            try:
                await update.callback_query.message.delete()
            except Exception:
                pass
            await update.callback_query.message.reply_text(
                text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.callback_query.edit_message_text(
                text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
            )
    else:
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def show_subcategories(query, category: str):
    user_id = query.from_user.id if hasattr(query, "from_user") and query.from_user else 0
    show_inactive = is_admin(user_id)
    subcats = db.get_subcategories(category, include_inactive=show_inactive)

    keyboard = []
    row = []
    for sub in subcats:
        sub_path = f"{category} > {sub}"
        is_sub_active = db.is_category_active(sub_path)
        tag = " [OFF 🔴]" if (not is_sub_active and show_inactive) else ""
        sub_courses = db.get_courses_by_filter(category=category, subcategory=sub, include_inactive=show_inactive)
        count = len(sub_courses)
        row.append(InlineKeyboardButton(f"{sub}{tag} ({count})", callback_data=f"subcat_{category}_{sub}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # Direct courses in root category (if any)
    direct_courses = db.get_courses_by_folder(category, include_inactive=show_inactive)
    for course in direct_courses:
        price_tag = f" ({course['price']} ৳)" if course.get('price', 0) > 0 else " 🆓"
        is_bought = " ✅" if db.is_purchased(user_id, course["id"]) else ""
        status_tag = "🔴 " if (course.get("status") == "inactive" and show_inactive) else ""
        keyboard.append([
            InlineKeyboardButton(
                f"{status_tag}{course['name']}{price_tag}{is_bought}",
                callback_data=f"course_{course['id']}"
            )
        ])

    # All courses under this category
    if db.is_view_all_courses_enabled():
        all_courses = db.get_courses_by_filter(category=category, include_inactive=show_inactive)
        keyboard.append([InlineKeyboardButton(f"🎓 View All {category} Courses ({len(all_courses)})", callback_data=f"subcat_{category}_ALL")])

    if is_admin(user_id):
        keyboard.append([
            InlineKeyboardButton(f"➕ Add Course", callback_data=f"adm_addcourse_cat_{category}"),
            InlineKeyboardButton("📁 Manage Folder", callback_data=f"adm_dir_{category}")
        ])

    keyboard.append([InlineKeyboardButton("◀️ Back", callback_data="back_to_categories")])

    if subcats and direct_courses:
        text = f"📂 **{category} এর বিষয় ও কোর্সসমূহ:**"
    elif direct_courses:
        text = f"📚 **{category} এর কোর্সসমূহ:**"
    else:
        text = f"📂 **{category} এর বিষয়/প্রোগ্রাম:**"

    if query.message.photo:
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.reply_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )


# ==================== COURSE DETAILS & PURCHASE ====================

def safe_html_format(text: str) -> str:
    valid_tags_regex = re.compile(
        r'(</?(?:b|i|u|code|pre|blockquote|a)(?:\s+href="[^"]*")?\s*>)',
        re.IGNORECASE
    )
    parts = valid_tags_regex.split(text)
    for i in range(len(parts)):
        if i % 2 == 0:
            parts[i] = html.escape(parts[i])
    return "".join(parts)


def format_course_card_html(name: str, price, description: str, discount: Optional[int] = None) -> str:
    name_clean = html.escape(str(name).strip())
    if discount is not None:
        price_str = f"<s>৳{price}</s> ➔ <b>৳{discount} BDT</b> 🔥 <i>(কুপন ডিসকাউন্ট প্রয়োগ করা হয়েছে!)</i>"
    else:
        try:
            p_val = int(price)
            price_str = f"<b>৳{p_val} BDT</b>" if p_val > 0 else "<b>১০০% ফ্রি (Free Course) 🎁</b>"
        except Exception:
            price_str = f"<b>৳{price} BDT</b>"
        
    desc_clean = safe_html_format(str(description).strip())
    if not desc_clean:
        desc_clean = "কোর্সের বিস্তারিত তথ্য শীঘ্রই যুক্ত করা হবে।"
        
    desc_display = desc_clean
        
    return f"""<blockquote><b>{name_clean}</b></blockquote>

<blockquote>💰 <b>Price:</b> {price_str}</blockquote>

{desc_display}"""


async def send_rich_course_message(target, html_text: str, reply_markup: InlineKeyboardMarkup, image: str = None, is_edit: bool = False):
    is_callback = hasattr(target, "message")
    
    if image:
        if len(html_text) <= 1024:
            if is_callback:
                try:
                    await target.message.delete()
                except Exception:
                    pass
                try:
                    return await target.message.reply_photo(photo=image, caption=html_text, parse_mode="HTML", reply_markup=reply_markup)
                except Exception as e:
                    logger.warning(f"Failed to reply_photo with caption: {e}")
                    return await target.message.reply_text(html_text, parse_mode="HTML", reply_markup=reply_markup)
            else:
                try:
                    return await target.reply_photo(photo=image, caption=html_text, parse_mode="HTML", reply_markup=reply_markup)
                except Exception as e:
                    logger.warning(f"Failed to reply_photo with caption: {e}")
                    return await target.reply_text(html_text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            if is_callback:
                try:
                    await target.message.delete()
                except Exception:
                    pass
                try:
                    await target.message.reply_photo(photo=image)
                except Exception:
                    pass
                return await target.message.reply_text(html_text, parse_mode="HTML", reply_markup=reply_markup)
            else:
                try:
                    await target.reply_photo(photo=image)
                except Exception:
                    pass
                return await target.reply_text(html_text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        if is_callback:
            if is_edit and not target.message.photo:
                try:
                    return await target.edit_message_text(html_text, parse_mode="HTML", reply_markup=reply_markup)
                except Exception:
                    pass
            try:
                await target.message.delete()
            except Exception:
                pass
            return await target.message.reply_text(html_text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            return await target.reply_text(html_text, parse_mode="HTML", reply_markup=reply_markup)


async def send_course_details(target, context: ContextTypes.DEFAULT_TYPE, course: dict, user_id: int, is_edit=False):
    course_id = course["id"]
    is_purchased = db.is_purchased(user_id, course_id)
    price = course.get("price", 0)
    discount = context.user_data.get("discounted_price") if context.user_data.get("coupon_course") == course_id else None

    category = course.get('category', 'HSC 28')
    subcategory = course.get('subcategory', course.get('program', 'General'))

    msg = format_course_card_html(course.get('name', ''), price, course.get('description', ''), discount)

    keyboard = []
    if is_purchased:
        msg += "\n\n🎉 <b>আপনি ইতিমধ্যে এই কোর্সে এনরোল করেছেন!</b>"
        access_link = course.get("access_link", "")
        if access_link:
            dyn_link = await get_dynamic_access_link(context.bot, access_link, user_id)
            if dyn_link:
                keyboard.append([InlineKeyboardButton("➥ Go to Course", url=dyn_link)])
        share_url = f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}?start=course_{course_id}&text={quote_plus(course.get('name', ''))}"
        keyboard.append([
            InlineKeyboardButton("◀️ Back", callback_data=f"cat_{category}"),
            InlineKeyboardButton("📤 Share", url=share_url)
        ])
    else:
        if price == 0:
            keyboard.append([InlineKeyboardButton("🎁 Free Enroll", callback_data=f"free_enroll_{course_id}")])
            share_url = f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}?start=course_{course_id}&text={quote_plus(course.get('name', ''))}"
            keyboard.append([
                InlineKeyboardButton("◀️ Back", callback_data=f"cat_{category}"),
                InlineKeyboardButton("📤 Share", url=share_url)
            ])
        else:
            keyboard.append([
                InlineKeyboardButton("🎟 Coupon", callback_data=f"coupon_{course_id}"),
                InlineKeyboardButton("🛒 Add to Cart", callback_data=f"addcart_{course_id}")
            ])
            buy_price = discount if discount is not None else price
            keyboard.append([
                InlineKeyboardButton(f"🛍 Buy Now — ৳{buy_price}", callback_data=f"buy_{course_id}")
            ])
            share_url = f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}?start=course_{course_id}&text={quote_plus(course.get('name', ''))}"
            keyboard.append([
                InlineKeyboardButton("◀️ Back", callback_data=f"cat_{category}"),
                InlineKeyboardButton("📤 Share", url=share_url)
            ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    image = course.get("image")
    await send_rich_course_message(target, msg, reply_markup, image=image, is_edit=is_edit)


async def send_admin_course_preview(target, context: ContextTypes.DEFAULT_TYPE, new_course: dict):
    context.user_data["course_step"] = "preview"
    name = new_course.get("name", "Untitled Course")
    price = new_course.get("price", 0)
    description = new_course.get("description", "")
    category = new_course.get("category", "General")
    subcategory = new_course.get("subcategory", new_course.get("program", "General"))
    link = new_course.get("access_link", "")
    image = new_course.get("image", "")

    card_text = format_course_card_html(name, price, description)
    
    meta_info = f"\n\n📂 <b>Category:</b> {html.escape(str(category))} | {html.escape(str(subcategory))}"
    if link:
        meta_info += f"\n🔗 <b>Access Link:</b> {html.escape(str(link))}"
    else:
        meta_info += "\n🔗 <b>Access Link:</b> <i>দেওয়া হয়নি</i>"

    preview_msg = f"✨ <b>[ Course Preview / প্রিভিউ ]</b>\n━━━━━━━━━━━━━━━━━━━━\n{card_text}{meta_info}"

    keyboard = [
        [InlineKeyboardButton("✅ Publish Course (পাবলিশ করুন)", callback_data="adm_pub_course")],
        [InlineKeyboardButton("🖼️ Add / Change Photo", callback_data="adm_chg_img"), InlineKeyboardButton("📂 Change Category", callback_data="adm_chg_cat")],
        [InlineKeyboardButton("✏️ Edit Details", callback_data="adm_chg_fields"), InlineKeyboardButton("❌ Cancel (বাতিল)", callback_data="adm_cancel_course")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_rich_course_message(target, preview_msg, reply_markup, image=image, is_edit=False)


async def send_admin_category_selector(target, context: ContextTypes.DEFAULT_TYPE, title_prefix: str = "📂 **ধাপ ৪/৬: ক্যাটাগরি নির্বাচন করুন:**"):
    cats = db.get_categories()
    keyboard = []
    row = []
    for cat in cats:
        row.append(InlineKeyboardButton(f"◈ {cat}", callback_data=f"adm_setcat_{cat}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("➕ Custom / New Category", callback_data="adm_setcat_CUSTOM")])
    keyboard.append([InlineKeyboardButton("« Back", callback_data="adm_course_back"), InlineKeyboardButton("✕ Cancel", callback_data="adm_course_cancel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = f"{title_prefix}\n\n👇 নিচের তালিকা থেকে ক্যাটাগরি সিলেক্ট করুন:"
    if hasattr(target, "message"):
        if target.message.photo:
            try:
                await target.message.delete()
            except Exception:
                pass
            await target.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            try:
                await target.edit_message_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
            except Exception:
                await target.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await target.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)


async def send_admin_subcategory_selector(target, context: ContextTypes.DEFAULT_TYPE, cat_selected: str):
    subcats = db.get_subcategories(cat_selected)
    keyboard = []
    row = []
    for s in subcats:
        row.append(InlineKeyboardButton(f"• {s}", callback_data=f"adm_setsub_{s}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("➕ Custom / New Sub-Category", callback_data="adm_setsub_CUSTOM")])
    keyboard.append([InlineKeyboardButton("« Back", callback_data="adm_course_back"), InlineKeyboardButton("✕ Cancel", callback_data="adm_course_cancel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = f"📂 **Category:** `{cat_selected}`\n\n🎯 **ধাপ ৫/৬: সাব-ক্যাটাগরি নির্বাচন করুন:**"
    if hasattr(target, "message"):
        if target.message.photo:
            try:
                await target.message.delete()
            except Exception:
                pass
            await target.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            try:
                await target.edit_message_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
            except Exception:
                await target.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await target.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)


# ==================== PROFILE, EBOOKS & EARNINGS ====================

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = db.get_user(user.id) or {}
    purchased_courses = user_data.get("purchased_courses", [])
    purchased_ebooks = user_data.get("purchased_ebooks", [])
    orders = db.get_user_orders(user.id)
    total_spent = sum(o.get("amount", 0) for o in orders if o.get("status") == "approved")
    wallet_balance = user_data.get("balance", 0)

    name = html.escape(user.first_name or 'User')
    username = f"@{html.escape(user.username)}" if user.username else "N/A"

    msg = f"""Hi, <b>{name}</b> 👋

🆔 <b>User ID:</b> <code>{user.id}</code>
🔗 <b>Username:</b> {username}

🎓 <b>Courses:</b> {len(purchased_courses)}
📚 <b>E-Books:</b> {len(purchased_ebooks)}
💰 <b>Wallet Balance:</b> ৳{wallet_balance} BDT
💳 <b>Total Spent:</b> ৳{total_spent} BDT"""

    keyboard = [
        [InlineKeyboardButton("🎓 My Courses", callback_data="my_courses_nav"), InlineKeyboardButton("📚 My E-Books", callback_data="my_ebooks_nav")],
        [InlineKeyboardButton("🧾 Order History", callback_data="my_orders_history"), InlineKeyboardButton("🎁 Refer & Earn", callback_data="my_refer_nav")]
    ]

    # Show Earnings & Withdraw button ONLY to users with earnings_enabled explicitly turned on by Admin
    if db.is_earnings_enabled(user.id):
        keyboard.append([InlineKeyboardButton("💰 Earnings & Withdraw", callback_data="my_earnings_nav")])

    if db.is_home_button_enabled():
        keyboard.append([InlineKeyboardButton("⚜️ HOME", callback_data="back_to_main_menu")])

    if update.callback_query:
        if update.callback_query.message.photo:
            try:
                await update.callback_query.message.delete()
            except Exception:
                pass
            await update.callback_query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.callback_query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bot_info = await context.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user.id}"
    user_data = db.get_user(user.id) or {}
    ref_count = user_data.get("referral_count", 0)
    ref_balance = user_data.get("balance", 0)
    bonus_amount = db.get_referral_reward_amount()
    is_enabled = db.is_referral_enabled()

    ref_nav_row = [InlineKeyboardButton("◀️ Profile", callback_data="profile_nav")]
    if db.is_home_button_enabled():
        ref_nav_row.append(InlineKeyboardButton("⚜️ HOME", callback_data="back_to_main_menu"))

    if not is_enabled:
        msg = """🎁 <b>Refer & Earn</b>
━━━━━━━━━━━━━━━━━━━━

⚠️ <i>The referral program is currently suspended. Please check back later.</i>"""
        keyboard = [ref_nav_row]
    else:
        msg = f"""🎁 <b>Refer & Earn</b>
━━━━━━━━━━━━━━━━━━━━

Share your referral link with friends. When someone purchases a course using your link, you will earn a <b>৳{bonus_amount} BDT</b> bonus!

👥 <b>Successful Referrals:</b> <code>{ref_count}</code>
💰 <b>Referral Balance:</b> <code>৳{ref_balance} BDT</code>

🔗 <b>Your Referral Link:</b>
<code>{ref_link}</code>"""

        keyboard = [
            [InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={ref_link}&text=StudyMart - Best Courses:")],
            ref_nav_row
        ]

    if update.callback_query:
        if update.callback_query.message.photo:
            try:
                await update.callback_query.message.delete()
            except Exception:
                pass
            await update.callback_query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.callback_query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== EARNINGS & WITHDRAW DASHBOARD ====================

async def show_earnings_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = db.get_user(user.id) or {}
    balance = user_data.get("balance", 0)

    if not db.is_earnings_enabled(user.id):
        msg = f"""💰 <b>ওয়ালেট ব্যালেন্স (Wallet Balance)</b>
━━━━━━━━━━━━━━━━━━━━

💳 <b>আপনার বর্তমান ব্যালেন্স:</b> <code>৳{balance} BDT</code>

💡 <i>আপনার অর্জিত ব্যালেন্স দিয়ে আপনি বটের যেকোনো কোর্স বা ই-বুক সরাসরি ক্রয় করতে পারবেন। ক্যাশ উত্তোলন সুবিধা শুধুমাত্র অনুমোদিত ব্যবহারকারীদের জন্য।</i>"""
        keyboard = [
            [InlineKeyboardButton("🎓 কোর্স ব্রাউজ করুন", callback_data="browse_categories")],
            [InlineKeyboardButton("◀️ Profile", callback_data="profile_nav")]
        ]
        if update.callback_query:
            if update.callback_query.message.photo:
                try:
                    await update.callback_query.message.delete()
                except Exception:
                    pass
                await update.callback_query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await update.callback_query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    msg = f"""💰 **উপার্জন ও উত্তোলন ড্যাশবোর্ড (Earnings)**

💳 **বর্তমান ব্যালেন্স (Available Balance):**
👉 ৳{balance} BDT

💡 আপনার অর্জিত রেফারেল ব্যালেন্স এখান থেকে সরাসরি উত্তোলন করতে পারবেন (সর্বনিম্ন ৳{MIN_WITHDRAW_AMOUNT} BDT)।"""

    keyboard = [
        [InlineKeyboardButton("💸 Withdraw Balance", callback_data="earn_withdraw")],
        [InlineKeyboardButton("🧾 Earnings History", callback_data="earn_history"), InlineKeyboardButton("💸 Withdrawal History", callback_data="earn_withdraw_history")],
        [InlineKeyboardButton("◀️ Profile", callback_data="profile_nav")]
    ]

    if update.callback_query:
        if update.callback_query.message.photo:
            try:
                await update.callback_query.message.delete()
            except Exception:
                pass
            await update.callback_query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.callback_query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_earnings_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    history = db.get_earnings_history(user_id)

    if not history:
        msg = """📊 **Earnings History (উপার্জনের হিসাব):**
━━━━━━━━━━━━━━━━━━━━

বর্তমানে আপনার কোনো উপার্জনের রেকর্ড নেই।
আপনার রেফারেল কুপন ব্যবহার করে কোনো কোর্স ক্রয় সফল হলে এখানে যুক্ত হবে।"""
    else:
        msg = "📊 **Earnings History (উপার্জনের হিসাব):**\n━━━━━━━━━━━━━━━━━━━━\n\n"
        for item in history[:15]:
            msg += f"• **+{item['amount']}৳** — `{item.get('coupon_code', 'REWARD')}` ({item.get('date', '')[:10]})\n"

    keyboard = [
        [InlineKeyboardButton("« Earnings Menu", callback_data="my_earnings_nav")]
    ]

    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_withdrawal_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    withdrawals = db.get_user_withdrawals(user_id)

    if not withdrawals:
        msg = """💳 **Withdrawal History (উত্তোলনের রেকর্ড):**
━━━━━━━━━━━━━━━━━━━━

আপনি এখনও কোনো উইথড্রয়াল রিকোয়েস্ট পাঠাননি।"""
    else:
        msg = "💳 **Withdrawal History (উত্তোলনের রেকর্ড):**\n━━━━━━━━━━━━━━━━━━━━\n\n"
        for w in withdrawals[:10]:
            st = "🟡 Pending" if w.get("status") == "pending" else "✅ Approved" if w.get("status") == "approved" else "❌ Rejected"
            msg += f"• **{w['amount']}৳** | {w.get('method', 'N/A')} ({w.get('account', 'N/A')})\n  📅 {w.get('date', '')[:16]} | স্ট্যাটাস: **{st}**\n\n"

    keyboard = [
        [InlineKeyboardButton("« Earnings Menu", callback_data="my_earnings_nav")]
    ]

    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def start_withdraw_flow(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    if not db.is_earnings_enabled(user_id):
        await query.answer("⚠️ আপনার অ্যাকাউন্টে ক্যাশ উত্তোলন সুবিধা সক্রিয় নেই। ব্যালেন্স দিয়ে যেকোনো কোর্স বা ই-বুক সরাসরি কিনতে পারবেন।", show_alert=True)
        return

    user_data = db.get_user(user_id) or {}
    balance = user_data.get("balance", 0)

    if balance < MIN_WITHDRAW_AMOUNT:
        await query.answer(f"⚠️ সর্বনিম্ন উত্তোলনযোগ্য ব্যালেন্স {MIN_WITHDRAW_AMOUNT} ৳! আপনার ব্যালেন্স: {balance} ৳", show_alert=True)
        return

    context.user_data["withdraw_step"] = "amount"
    msg = f"""💸 **উইথড্রয়াল রিকোয়েস্ট (Withdraw Balance)**
━━━━━━━━━━━━━━━━━━━━

💰 **আপনার বর্তমান ব্যালেন্স:** `{balance}` ৳
📌 **সর্বনিম্ন উত্তোলন:** `{MIN_WITHDRAW_AMOUNT}` ৳

👇 **কত টাকা উত্তোলন করতে চান তা সংখ্যায় লিখে মেসেজ পাঠান:**
(যেমন: `100` বা `350`)

💡 বাতিল করতে চাইলে /cancel লিখুন।"""

    await query.edit_message_text(msg, parse_mode="Markdown")


async def show_my_courses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    courses = db.get_user_courses(user_id)

    if not courses:
        msg = """🎓 <b>No Courses Found!</b>

You have not enrolled in any courses yet. Click below to browse available courses."""
        keyboard = [
            [InlineKeyboardButton("📚 Browse Courses", callback_data="browse_categories")],
            [InlineKeyboardButton("◀️ Profile", callback_data="profile_nav")]
        ]
        if update.callback_query:
            if update.callback_query.message.photo:
                try:
                    await update.callback_query.message.delete()
                except Exception:
                    pass
                await update.callback_query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await update.callback_query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    msg = f"🎓 <b>My Courses ({len(courses)}):</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    keyboard = []

    for idx, c in enumerate(courses, 1):
        c_name = html.escape(c.get('name', 'Course'))
        msg += f"{idx}. <b>{c_name}</b>\n"
        link = c.get("access_link", "")
        if link:
            dyn_link = await get_dynamic_access_link(context.bot, link, user_id)
            if dyn_link:
                keyboard.append([InlineKeyboardButton(f"{c['name']}", url=dyn_link)])
        else:
            keyboard.append([InlineKeyboardButton(f"{c['name']}", callback_data=f"course_{c['id']}")])

    msg += "\n💡 <i>Click the buttons below to access your course.</i>"
    keyboard.append([InlineKeyboardButton("◀️ Profile", callback_data="profile_nav")])

    if update.callback_query:
        if update.callback_query.message.photo:
            try:
                await update.callback_query.message.delete()
            except Exception:
                pass
            await update.callback_query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.callback_query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_my_ebooks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ebooks = db.get_user_ebooks(user_id)
    all_ebs = db.get_all_ebooks()
    free_ebs = [eb for eid, eb in all_ebs.items() if eb.get("price", 0) == 0]

    keyboard = []
    if not ebooks:
        if free_ebs:
            msg = f"📚 **ফ্রি ই-বুক ও স্টাডি মেটেরিয়াল ({len(free_ebs)} টি উপলব্ধ):**\n\n"
            for idx, eb in enumerate(free_ebs, 1):
                msg += f"{idx}. 📘 **{eb['name']}** (Free)\n"
                eid = eb.get("id") or list(all_ebs.keys())[idx - 1]
                if eb.get("file_id"):
                    keyboard.append([InlineKeyboardButton(f"📥 Download {eb['name'][:16]} (PDF)", callback_data=f"ebdl_{eid}")])
                elif eb.get("access_link"):
                    keyboard.append([InlineKeyboardButton(f"📥 Download {eb['name'][:16]}", url=eb["access_link"])])
        else:
            msg = """📚 **আমার ই-বুক ও স্টাডি মেটেরিয়াল**

📖 আপনার অ্যাকাউন্টে বর্তমানে কোনো ই-বুক নেই।
খুব শীঘ্রই এখানে প্রিমিয়াম ই-বুক ও লেকচার নোটস যুক্ত করা হবে।"""
            keyboard.append([InlineKeyboardButton("📚 Browse Courses", callback_data="browse_categories")])
    else:
        msg = f"📚 **আপনার সংগৃহীত ই-বুকসমূহ ({len(ebooks)} টি):**\n\n"
        for idx, eb in enumerate(ebooks, 1):
            msg += f"{idx}. 📘 **{eb['name']}**\n"
            eid = eb.get("id", f"eb_{idx}")
            if eb.get("file_id"):
                keyboard.append([InlineKeyboardButton(f"📥 Download {eb['name'][:16]} (PDF)", callback_data=f"ebdl_{eid}")])
            else:
                link = eb.get("download_link", eb.get("access_link", ""))
                if link:
                    keyboard.append([InlineKeyboardButton(f"📥 Download {eb['name'][:16]}", url=link)])

    keyboard.append([InlineKeyboardButton("◀️ Profile", callback_data="profile_nav")])

    if update.callback_query:
        if update.callback_query.message.photo:
            try:
                await update.callback_query.message.delete()
            except Exception:
                pass
            await update.callback_query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.callback_query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def browse_ebooks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cats = db.get_ebook_categories()
    all_ebs = db.get_all_ebooks()
    
    msg = """📚 **StudyMart ই-বুক ও স্টাডি মেটেরিয়াল লাইব্রেরী**
━━━━━━━━━━━━━━━━━━━━
📖 আপনার প্রয়োজনীয় ক্যাটাগরি বা বিষয় নির্বাচন করুন:"""
    keyboard = []
    row = []
    for cat in cats:
        eb_count = len(db.get_ebooks_by_category(cat))
        row.append(InlineKeyboardButton(f"📁 {cat} ({eb_count})", callback_data=f"ebcat_{cat}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    if len(all_ebs) > 0:
        keyboard.append([InlineKeyboardButton(f"📖 All E-Books ({len(all_ebs)})", callback_data="ebcat_ALL")])

    if db.is_home_button_enabled():
        keyboard.append([InlineKeyboardButton("⚜️ HOME", callback_data="back_to_main_menu")])

    if query:
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def show_ebooks_by_category(update: Update, context: ContextTypes.DEFAULT_TYPE, cat: str):
    query = update.callback_query
    user_id = update.effective_user.id

    clean_path = str(cat).strip().replace(" / ", " > ").replace("/", " > ")
    if clean_path.upper() == "ALL":
        ebooks = [dict(eb, id=eid) for eid, eb in db.get_all_ebooks().items() if eb.get("status") != "inactive"]
        subfolders = []
    else:
        subfolders = db.get_ebook_sub_folders(clean_path)
        ebooks = db.get_ebooks_by_folder(clean_path, include_inactive=False)

    segments = [s.strip() for s in clean_path.split(" > ") if s.strip()]
    parent_path = " > ".join(segments[:-1]) if len(segments) > 1 else ""

    cat_title = "সকল ই-বুক" if clean_path.upper() == "ALL" else f"ক্যাটাগরি: {' ➔ '.join(segments)}"
    msg = f"""📁 **{cat_title}**
━━━━━━━━━━━━━━━━━━━━
আপনার পছন্দের ই-বুক বা স্টাডি মেটেরিয়াল নির্বাচন করুন:"""

    keyboard = []

    # Subfolders (if any)
    row = []
    for sf in subfolders:
        child_path = f"{clean_path} > {sf}" if clean_path else sf
        child_ebooks = db.get_ebooks_by_folder(child_path, include_inactive=False)
        child_sub_count = len(db.get_ebook_sub_folders(child_path))
        total_items = len(child_ebooks) if len(child_ebooks) > 0 else child_sub_count
        badge = f" ({total_items})" if total_items > 0 else ""
        row.append(InlineKeyboardButton(f"📁 {sf}{badge}", callback_data=f"ebcat_{child_path}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # E-Books in this folder
    for eb in ebooks:
        eid = eb.get("id")
        price = eb.get("price", 0)
        price_tag = f" (৳{price})" if price > 0 else " (Free)"

        if db.has_user_ebook_access(user_id, eid):
            keyboard.append([InlineKeyboardButton(f"✅ {eb['name']} (সংগৃহীত)", callback_data=f"view_eb_{eid}")])
        else:
            keyboard.append([InlineKeyboardButton(f"• {eb['name']}{price_tag}", callback_data=f"view_eb_{eid}")])

    if not subfolders and not ebooks:
        msg += "\n\n*(বর্তমানে এই ক্যাটাগরিতে কোনো ই-বুক নেই)*"

    if len(segments) > 1:
        keyboard.append([InlineKeyboardButton(f"« Back to {segments[-2]}", callback_data=f"ebcat_{parent_path}")])
    keyboard.append([InlineKeyboardButton("« Back to Categories", callback_data="browse_ebooks")])
    if db.is_home_button_enabled():
        keyboard.append([InlineKeyboardButton("⚜️ HOME", callback_data="back_to_main_menu")])

    if query:
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def view_ebook_details(update: Update, context: ContextTypes.DEFAULT_TYPE, eb_id: str):
    query = update.callback_query
    eb = db.get_ebook(eb_id)
    if not eb:
        if query:
            await query.answer("E-Book not found!", show_alert=True)
        return

    user_id = update.effective_user.id
    price = eb.get("price", 0)
    has_access = db.has_user_ebook_access(user_id, eb_id)
    cat = eb.get("category", "General")

    msg = f"""📖 **{eb['name']}**
━━━━━━━━━━━━━━━━━━━━

📂 **ক্যাটাগরি:** `{cat}`
💰 **মূল্য:** {f"৳{price}" if price > 0 else "বিনামূল্যে (Free)"}

📝 **বিবরণ:**
{eb.get('description', 'প্রিমিয়াম ই-বুক ও স্টাডি মেটেরিয়াল।')}"""

    keyboard = []
    if has_access:
        if eb.get("file_id"):
            keyboard.append([InlineKeyboardButton("📥 Download PDF (ফাইল ডাউনলোড)", callback_data=f"ebdl_{eb_id}")])
        if eb.get("access_link"):
            keyboard.append([InlineKeyboardButton("🔗 Open Drive Link", url=eb["access_link"])])
    else:
        if price == 0:
            keyboard.append([InlineKeyboardButton("🎁 Get Free Access (সংগ্রহ করুন)", callback_data=f"unlock_free_eb_{eb_id}")])
        else:
            keyboard.append([InlineKeyboardButton(f"💳 Buy E-Book — ৳{price}", callback_data=f"buy_eb_{eb_id}")])

    keyboard.append([InlineKeyboardButton("« Back", callback_data=f"ebcat_{cat}")])
    if db.is_home_button_enabled():
        keyboard.append([InlineKeyboardButton("⚜️ HOME", callback_data="back_to_main_menu")])

    if query:
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== CART OPERATIONS ====================

async def show_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    items = db.get_cart(user_id)

    if not items:
        msg = "🛒 **Your shopping cart is empty!**\n\nTo add courses, please visit the course details page and press the **Add to Cart** button."
        keyboard = [
            [InlineKeyboardButton("📚 Browse Courses", callback_data="browse_categories")]
        ]
        if db.is_home_button_enabled():
            keyboard.append([InlineKeyboardButton("⚜️ HOME", callback_data="back_to_main_menu")])

        if update.callback_query:
            if update.callback_query.message.photo:
                try:
                    await update.callback_query.message.delete()
                except Exception:
                    pass
                await update.callback_query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await update.callback_query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    total = sum(item.get("price", 0) for item in items)
    original_total = total
    applied_coupon = context.user_data.get("applied_coupon")
    discount_val = 0
    if applied_coupon:
        course_ids = [i['id'] for i in items]
        valid, discount, message, coupon_obj = db.validate_coupon_advanced(
            applied_coupon, user_id, total, "All", course_ids=course_ids
        )
        if valid:
            discount_val = discount
            total = max(0, total - discount)
        else:
            context.user_data.pop("applied_coupon", None)
            applied_coupon = None

    msg = f"🛒 **শপিং কার্ট ({len(items)} টি আইটেম):**\n\n"
    keyboard = []

    for idx, item in enumerate(items, 1):
        msg += f"{idx}.  **{item['name']}** - {item['price']} ৳\n"
        keyboard.append([
            InlineKeyboardButton(f"❌ Remove: {item['name'][:18]}", callback_data=f"remcart_{item['id']}")
        ])

    if discount_val > 0:
        msg += f"\n💰 **মোট মূল্য:** {original_total} ৳ BDT\n"
        msg += f"🎟 **কুপন ছাড়:** -{discount_val} ৳ (`{applied_coupon}`)\n"
        msg += f"✅ **সর্বমোট পরিশোধযোগ্য:** **{total} ৳ BDT**"
    else:
        msg += f"\n💰 **সর্বমোট মূল্য:** **{total} ৳ BDT**"

    coupon_btn_text = f"🎟 Coupon: {applied_coupon} ✅" if applied_coupon else "🎟 Apply Coupon"
    keyboard.append([
        InlineKeyboardButton(coupon_btn_text, callback_data="coupon_cart"),
        InlineKeyboardButton("🛍 Checkout All", callback_data="checkout_cart")
    ])
    keyboard.append([InlineKeyboardButton("🗑 Clear Cart", callback_data="clear_cart")])
    if db.is_home_button_enabled():
        keyboard.append([InlineKeyboardButton("⚜️ HOME", callback_data="back_to_main_menu")])

    if update.callback_query:
        if update.callback_query.message.photo:
            try:
                await update.callback_query.message.delete()
            except Exception:
                pass
            await update.callback_query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.callback_query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== INFO & SUPPORT MENU ====================

def get_default_info_content(item_key: str) -> str:
    if item_key == "contact":
        return db.get_setting("support_message") or (
            f"➥ **Contact Support / হেল্পলাইন**\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"যেকোনো কোর্স সংক্রান্ত তথ্য, পেমেন্ট হেল্প বা সমস্যার জন্য আমাদের সাপোর্ট টিমের সাথে যোগাযোগ করুন:\n\n"
            f"👨‍💻 **Admin Support:** @{SUPPORT_USERNAME}\n"
            f"📢 **Official Channel:** @{BOT_USERNAME}\n"
            f"⏰ **সাপোর্ট সময়:** সকাল ৯:০০ টা - রাত ১২:০০ টা\n\n"
            f"💡 আপনার কোনো সমস্যা থাকলে এডমিনের ইনবক্সে সরাসরি মেসেজ দিন।"
        )
    elif item_key == "how_to_buy":
        return (
            "❖ **How to Buy / কোর্স কেনার নিয়ম**\n━━━━━━━━━━━━━━━━━━━━\n\n"
            "আমাদের প্ল্যাটফর্ম থেকে খুব সহজেই যেকোনো কোর্স কিনতে পারবেন। নিচের ধাপগুলো অনুসরণ করুন:\n\n"
            "1️⃣ **কোর্স নির্বাচন:** \n"
            "মেনু বা সার্চ থেকে আপনার পছন্দের কোর্সটি সিলেক্ট করুন।\n\n"
            "2️⃣ **অর্ডার শুরু করুন:** \n"
            "কোর্সের বিস্তারিত দেখে **'💳 এখনই কিনুন (Buy Now)'** বাটনে চাপ দিন। (কুপন থাকলে আগে কুপন প্রয়োগ করতে পারেন)\n\n"
            "3️⃣ **পেমেন্ট মেথড নির্বাচন:** \n"
            "বিকাশ, নগদ বা রকেট এর মধ্যে আপনার সুবিধাজনক মাধ্যম বেছে নিন।\n\n"
            "4️⃣ **টাকা পাঠান (Send Money):** \n"
            "প্রদর্শিত নাম্বারে নির্দিষ্ট পরিমাণ টাকা Send Money করুন এবং প্রাপ্ত **TrxID (Transaction ID)** টি কপি করুন।\n\n"
            "5️⃣ **TrxID সাবমিট:** \n"
            "TrxID টি বটে সেন্ড করলেই আপনার অর্ডার সাবমিট হয়ে যাবে।\n\n"
            "6️⃣ **তাৎক্ষণিক অ্যাক্সেস:** \n"
            "এডমিন ট্রানজেকশন যাচাই করার সাথে সাথেই আপনার ইনবক্সে ক্লাসের প্রাইভেট লিংক ও ম্যাটেরিয়াল চলে আসবে!"
        )
    elif item_key == "about":
        return (
            f"◈ **About StudyMart**\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"**{BOT_NAME}** একটি সম্পূর্ণ আধুনিক এবং নির্ভরযোগ্য অনলাইন শিক্ষা সেবা প্ল্যাটফর্ম।\n\n"
            f"🎯 **আমাদের ভিশন:**\n"
            f"বাংলাদেশের যেকোনো প্রান্ত থেকে শিক্ষার্থীরা যাতে দেশের সেরা মেন্টরদের প্রিমিয়াম কোর্স, পূর্ণাঙ্গ লেকচার শিট ও পরীক্ষার প্রস্তুতি স্বল্প খরচে সহজেই গ্রহণ করতে পারে।\n\n"
            f"✦ **আমাদের ফিচারসমূহ:**\n"
            f"• লাইফটাইম অ্যাক্সেস সহ ফুল এইচডি ক্লাস\n"
            f"• প্র্যাকটিস শিট, দাগানো বই ও এক্সক্লুসিভ নোটস\n"
            f"• দ্রুত পেমেন্ট ভেরিফিকেশন ও অটোমেটিক ডেলিভারি\n"
            f"• ডেডিকেটেড স্টুডেন্ট সাপোর্ট"
        )
    elif item_key == "terms":
        return (
            "• **Terms & Policy / নীতিমালা ও শর্তাবলী**\n━━━━━━━━━━━━━━━━━━━━\n\n"
            "১. **কোর্স অ্যাক্সেস:** ক্রয়কৃত কোর্সের ম্যাটেরিয়াল ও লিংকে আপনি ফুল অ্যাক্সেস পাবেন।\n"
            "২. **পেমেন্ট ভেরিফিকেশন:** ভুল TrxID বা সঠিক টাকার কম পেমেন্ট করলে অর্ডার বাতিল হবে।\n"
            "৩. **নন-রিফান্ডেবল:** ডিজিটাল কোর্স মেটেরিয়াল ও অ্যাক্সেস লিংক সরবরাহ করার পর তা রিফান্ডযোগ্য নয়।\n"
            "৪. **কপিরাইট ও শেয়ারিং:** কোর্সের কন্টেন্ট অন্য কোথাও বিক্রয় বা বাণিজ্যিকভাবে শেয়ার করা সম্পূর্ণরূপে নিষিদ্ধ।\n"
            "৫. **সাপোর্ট:** কোনো সমস্যা হলে সর্বদা আমাদের অফিসিয়াল এডমিনের সাথে যোগাযোগ করবেন।"
        )
    return ""


def get_info_menu_keyboard():
    cfg = db.get_info_settings()
    items = cfg.get("items", {})
    keyboard = []

    for key, callback in [
        ("contact", "info_contact"),
        ("how_to_buy", "info_how_to_buy"),
        ("about", "info_about"),
        ("terms", "info_terms")
    ]:
        if items.get(key, {}).get("enabled", True):
            label = items.get(key, {}).get("label") or key
            keyboard.append([InlineKeyboardButton(label, callback_data=callback)])

    for b in cfg.get("custom_buttons", []):
        if b.get("enabled", True):
            b_label = b.get("label", "Button")
            b_type = b.get("type", "url")
            b_content = b.get("content", "")
            if b_type == "url" and (b_content.startswith("http://") or b_content.startswith("https://") or b_content.startswith("t.me")):
                url_dest = b_content if b_content.startswith("http") else f"https://{b_content}"
                keyboard.append([InlineKeyboardButton(b_label, url=url_dest)])
            else:
                keyboard.append([InlineKeyboardButton(b_label, callback_data=f"cinfo_view_{b['id']}")])

    if db.is_home_button_enabled():
        keyboard.append([InlineKeyboardButton("⚜️ HOME", callback_data="back_to_main_menu")])
    return InlineKeyboardMarkup(keyboard)


async def show_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = db.get_info_settings()
    default_info = "ℹ️ **StudyMart Help & Support**\n━━━━━━━━━━━━━━━━━━━━\n\nSelect an option below for purchasing guides, general questions, or direct support:"
    raw_text = cfg.get("header_text") or db.get_setting("info_message", default_info)
    if not raw_text or not str(raw_text).strip():
        raw_text = default_info
    text, parsed_kb = parse_inline_buttons(raw_text)
    if not text or not str(text).strip():
        text = default_info
    reply_markup = InlineKeyboardMarkup(parsed_kb) if parsed_kb else get_info_menu_keyboard()

    if update.callback_query:
        if update.callback_query.message.photo:
            try:
                await update.callback_query.message.delete()
            except Exception:
                pass
            await update.callback_query.message.reply_text(
                text, parse_mode="Markdown", reply_markup=reply_markup
            )
        else:
            await update.callback_query.edit_message_text(
                text, parse_mode="Markdown", reply_markup=reply_markup
            )
    else:
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=reply_markup
        )


# ==================== AUTOMATIC SEARCH (GENIUSHUB STYLE) ====================

async def auto_search_courses(update: Update, query: str):
    results = db.search_courses(query)

    if not results:
        default_fb = f"""🔍 **'{query}' সম্পর্কিত কোনো কোর্স বা ই-বুক খুঁজে পাওয়া যায়নি।**

💡 আপনি চাইলে সরাসরি নিচের বাটন থেকে সকল ক্যাটাগরি ও ক্লাস ব্রাউজ করতে পারেন:"""
        raw_fb = db.get_setting("fallback_message") or default_fb
        msg, parsed_kb = parse_inline_buttons(raw_fb)
        reply_markup = InlineKeyboardMarkup(parsed_kb) if parsed_kb else InlineKeyboardMarkup([
            [InlineKeyboardButton("❖ Browse All Courses", callback_data="browse_categories")],
            [InlineKeyboardButton("➥ Admin Support", url=f"https://t.me/{SUPPORT_USERNAME}")]
        ])
        await update.message.reply_text(
            msg,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        return

    keyboard = []
    for course in results[:10]:
        price_tag = f" ({course['price']} ৳)" if course.get('price', 0) > 0 else " 🆓"
        is_bought = " ✅" if db.is_purchased(user_id, course["id"]) else ""
        keyboard.append([
            InlineKeyboardButton(f"{course['name']}{price_tag}{is_bought}", callback_data=f"course_{course['id']}")
        ])

    keyboard.append([InlineKeyboardButton("❖ All Categories", callback_data="browse_categories")])

    msg = f"🔍 **'{query}' এর সার্চ ফলাফল ({len(results)} টি পাওয়া গেছে):**\n━━━━━━━━━━━━━━━━━━━━\nকোর্সের বিস্তারিত তথ্য ও ভর্তি হতে নিচের বাটনে চাপ দিন:"

    await update.message.reply_text(
        msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ==================== CALLBACK HANDLER ====================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    under_maint, maint_msg = check_maintenance(user_id)
    if under_maint:
        # Strip bold formatting for alert display
        clean_msg = maint_msg.replace("**", "").replace("__", "")
        await query.answer(clean_msg, show_alert=True)
        return

    data = query.data

    if not (data.startswith("addcart_") or data.startswith("remcart_") or data.startswith("copy_num_")):
        await query.answer()

    # Navigation & Info
    if data == "browse_categories" or data == "back_to_categories":
        await show_categories(update, context)

    elif data == "back_to_main_menu":
        try:
            if query.message.photo:
                await query.message.delete()
        except Exception:
            pass
            
        raw_welcome = db.get_setting("welcome_message", WELCOME_MESSAGE)
        cleaned_welcome, custom_kb = parse_inline_buttons(raw_welcome)
        
        if custom_kb:
            await query.message.reply_text(
                cleaned_welcome,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(custom_kb)
            )
        else:
            welcome_kb = get_welcome_inline_keyboard()
            if welcome_kb and welcome_kb.inline_keyboard:
                await query.message.reply_text(
                    raw_welcome,
                    parse_mode="Markdown",
                    reply_markup=welcome_kb
                )
            else:
                await query.message.reply_text(
                    raw_welcome,
                    parse_mode="Markdown",
                    reply_markup=main_menu_keyboard(user_id)
                )

    elif data == "profile_nav":
        await show_profile(update, context)

    elif data == "my_earnings_nav":
        await show_earnings_dashboard(update, context)

    elif data == "earn_history":
        await show_earnings_history(update, context)

    elif data == "earn_withdraw_history":
        await show_withdrawal_history(update, context)

    elif data == "earn_withdraw":
        await start_withdraw_flow(query, context, user_id)

    elif data.startswith("wdrpay_"):
        method_selected = data.replace("wdrpay_", "").title()
        context.user_data["withdraw_method"] = method_selected
        context.user_data["withdraw_step"] = "account"
        amount = context.user_data.get("withdraw_amount", 0)

        await query.edit_message_text(
            f"""💳 **{method_selected} একাউন্ট নাম্বার:**
━━━━━━━━━━━━━━━━━━━━

💰 উত্তোলনের পরিমাণ: `{amount}` ৳
💳 মেথড: **{method_selected}**

👇 আপনার **{method_selected} পার্সোনাল নাম্বারটি** লিখে মেসেজ পাঠান:
(যেমন: `017XXXXXXXX`)

💡 বাতিল করতে চাইলে /cancel লিখুন।""",
            parse_mode="Markdown"
        )

    elif data == "my_courses_nav":
        await show_my_courses(update, context)

    elif data == "my_ebooks_nav":
        await show_my_ebooks(update, context)

    elif data == "my_refer_nav":
        await show_referral(update, context)

    elif data == "view_cart_nav":
        await show_cart(update, context)

    elif data == "info_menu":
        await show_info(update, context)

    elif data == "info_contact":
        esc_support = SUPPORT_USERNAME.replace('_', '\\_')
        esc_bot = BOT_USERNAME.replace('_', '\\_')
        default_support = f"""➥ **Contact Support / হেল্পলাইন**
━━━━━━━━━━━━━━━━━━━━

যেকোনো কোর্স সংক্রান্ত তথ্য, পেমেন্ট হেল্প বা সমস্যার জন্য আমাদের সাপোর্ট টিমের সাথে যোগাযোগ করুন:

👨‍💻 **Admin Support:** @{esc_support}
📢 **Official Channel:** @{esc_bot}
⏰ **সাপোর্ট সময়:** সকাল ৯:০০ টা - রাত ১২:০০ টা

💡 আপনার কোনো সমস্যা থাকলে এডমিনের ইনবক্সে সরাসরি মেসেজ দিন।"""
        
        raw_text = db.get_setting("support_message", default_support)
        text, parsed_kb = parse_inline_buttons(raw_text)
        
        reply_markup = None
        if parsed_kb:
            reply_markup = InlineKeyboardMarkup(parsed_kb)
        else:
            keyboard = [
                [InlineKeyboardButton("➥ Contact Admin", url=f"https://t.me/{SUPPORT_USERNAME}")],
                [InlineKeyboardButton("📢 Official Channel", url=f"https://t.me/{BOT_USERNAME}")],
                [InlineKeyboardButton("« Back to Info Menu", callback_data="info_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

        # Auto-escape unescaped underscores to prevent Markdown parsing failure
        text = re.sub(r'(?<!\\)_', r'\_', text)

        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)

    elif data == "info_how_to_buy":
        cfg = db.get_info_settings()
        custom_c = cfg.get("items", {}).get("how_to_buy", {}).get("content", "").strip()
        raw_msg = custom_c if custom_c else get_default_info_content("how_to_buy")
        msg, parsed_kb = parse_inline_buttons(raw_msg)
        default_kb = [
            [InlineKeyboardButton("❖ Browse Courses", callback_data="browse_categories")],
            [InlineKeyboardButton("➥ Contact Admin", url=f"https://t.me/{SUPPORT_USERNAME}")],
            [InlineKeyboardButton("« Back to Info Menu", callback_data="info_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(parsed_kb) if parsed_kb else InlineKeyboardMarkup(default_kb)
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

    elif data == "info_about":
        cfg = db.get_info_settings()
        custom_c = cfg.get("items", {}).get("about", {}).get("content", "").strip()
        raw_msg = custom_c if custom_c else get_default_info_content("about")
        msg, parsed_kb = parse_inline_buttons(raw_msg)
        default_kb = [
            [InlineKeyboardButton("➥ Join Community", url=f"https://t.me/{BOT_USERNAME}")],
            [InlineKeyboardButton("« Back to Info Menu", callback_data="info_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(parsed_kb) if parsed_kb else InlineKeyboardMarkup(default_kb)
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

    elif data == "info_terms":
        cfg = db.get_info_settings()
        custom_c = cfg.get("items", {}).get("terms", {}).get("content", "").strip()
        raw_msg = custom_c if custom_c else get_default_info_content("terms")
        msg, parsed_kb = parse_inline_buttons(raw_msg)
        default_kb = [
            [InlineKeyboardButton("« Back to Info Menu", callback_data="info_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(parsed_kb) if parsed_kb else InlineKeyboardMarkup(default_kb)
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

    elif data.startswith("cinfo_view_"):
        c_id = data.replace("cinfo_view_", "")
        btn = db.get_custom_info_button(c_id)
        if btn:
            btn_lbl = html.escape(btn.get("label", "Info"))
            btn_cnt = html.escape(btn.get("content", ""))
            msg = f"ℹ️ <b>{btn_lbl}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{btn_cnt}"
            await query.edit_message_text(
                msg,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back to Info Menu", callback_data="info_menu")]])
            )
        else:
            await query.answer("Button not found!")

    # Category and Sub-category Browsing
    elif data.startswith("cat_"):
        category = data.replace("cat_", "")
        context.user_data["category"] = category
        if category == "ALL":
            show_inactive = is_admin(user_id)
            all_courses = db.get_courses_by_filter(include_inactive=show_inactive)
            keyboard = []
            for course in all_courses:
                price_tag = f" ({course['price']} ৳)" if course.get('price', 0) > 0 else " 🆓"
                is_bought = " ✅" if db.is_purchased(user_id, course["id"]) else ""
                status_tag = "🔴 " if (course.get("status") == "inactive" and show_inactive) else ""
                keyboard.append([
                    InlineKeyboardButton(
                        f"{status_tag}{course['name']}{price_tag}{is_bought}",
                        callback_data=f"course_{course['id']}"
                    )
                ])
            keyboard.append([InlineKeyboardButton("« Back to Categories", callback_data="browse_categories")])
            if db.is_home_button_enabled():
                keyboard.append([InlineKeyboardButton("⚜️ HOME", callback_data="back_to_main_menu")])

            msg_text = f"📚 **সকল কোর্সসমূহ ({len(all_courses)} টি):**\n━━━━━━━━━━━━━━━━━━━━\nযে কোর্সের তথ্য দেখতে চান সেটিতে ক্লিক করুন:"
            if query.message.photo:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await query.message.reply_text(msg_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await query.edit_message_text(msg_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await show_subcategories(query, category)

    elif data.startswith("subcat_"):
        parts = data.replace("subcat_", "").split("_", 1)
        category = parts[0]
        subcat = parts[1] if len(parts) > 1 else "ALL"
        context.user_data["category"] = category
        context.user_data["subcategory"] = subcat

        is_direct_all = False
        if subcat.endswith("_ALLDIRECT"):
            subcat = subcat.replace("_ALLDIRECT", "")
            is_direct_all = True

        if subcat != "ALL" and subcat != "" and not is_direct_all:
            nested_path = f"{category} > {subcat}"
            nested_subcats = db.get_subcategories(nested_path, include_inactive=is_admin(user_id))
            if nested_subcats:
                keyboard = []
                for child in nested_subcats:
                    child_path = f"{subcat} > {child}"
                    full_child_path = f"{category} > {child_path}"
                    is_child_active = db.is_category_active(full_child_path)
                    child_tag = " [OFF 🔴]" if (not is_child_active and is_admin(user_id)) else ""
                    child_count = len(db.get_courses_by_filter(category=category, subcategory=child_path, include_inactive=is_admin(user_id)))
                    keyboard.append([
                        InlineKeyboardButton(f"{child}{child_tag} ({child_count})", callback_data=f"subcat_{category}_{child_path}")
                    ])

                # Direct courses in this exact folder (e.g. FT EBI 4.0 in HSC 28 > Academy Program)
                direct_courses = db.get_courses_by_folder(nested_path, include_inactive=is_admin(user_id))
                for course in direct_courses:
                    price_tag = f" ({course['price']} ৳)" if course.get('price', 0) > 0 else " 🆓"
                    is_bought = " ✅" if db.is_purchased(user_id, course["id"]) else ""
                    status_tag = "🔴 " if (course.get("status") == "inactive" and is_admin(user_id)) else ""
                    keyboard.append([
                        InlineKeyboardButton(
                            f"{status_tag}{course['name']}{price_tag}{is_bought}",
                            callback_data=f"course_{course['id']}"
                        )
                    ])

                # View All Courses button for this subfolder/branch
                if db.is_view_all_courses_enabled():
                    sub_leaf_name = subcat.split(" > ")[-1]
                    all_sub_courses = db.get_courses_by_filter(category=category, subcategory=subcat, include_inactive=is_admin(user_id))
                    keyboard.append([
                        InlineKeyboardButton(f"🎓 View All {sub_leaf_name} Courses ({len(all_sub_courses)})", callback_data=f"subcat_{category}_{subcat}_ALLDIRECT")
                    ])

                parent_segs = subcat.split(" > ")
                back_cb = f"subcat_{category}_{' > '.join(parent_segs[:-1])}" if len(parent_segs) > 1 else f"cat_{category}"
                keyboard.append([InlineKeyboardButton("« Back", callback_data=back_cb)])

                if is_admin(user_id):
                    keyboard.append([
                        InlineKeyboardButton(f"➕ Add Course in '{subcat}'", callback_data=f"adm_addcourse_dir_{nested_path}"),
                        InlineKeyboardButton("📁 Manage Folder", callback_data=f"adm_dir_{nested_path}")
                    ])

                if direct_courses:
                    msg_text = f"📂 **{category} | {subcat} এর বিষয় ও কোর্সসমূহ:**\n━━━━━━━━━━━━━━━━━━━━\nআপনার প্রয়োজনীয় বিষয় বা কোর্স বেছে নিন:"
                else:
                    msg_text = f"📂 **{category} | {subcat} এর বিষয় / সাব-ক্যাটাগরি:**\n━━━━━━━━━━━━━━━━━━━━\nআপনার প্রয়োজনীয় বিষয় বা প্রোগ্রাম বেছে নিন:"

                if query.message.photo:
                    try:
                        await query.message.delete()
                    except Exception:
                        pass
                    await query.message.reply_text(msg_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
                else:
                    await query.edit_message_text(msg_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
                return

        sub_filter = None if subcat == "ALL" else subcat
        courses = db.get_courses_by_filter(category=category, subcategory=sub_filter, include_inactive=is_admin(user_id))

        sub_title = "সকল কোর্স" if subcat == "ALL" else subcat

        keyboard = []
        if not courses:
            parent_segs = subcat.split(" > ") if subcat != "ALL" else []
            back_cb = f"subcat_{category}_{' > '.join(parent_segs[:-1])}" if len(parent_segs) > 1 else f"cat_{category}"
            keyboard.append([InlineKeyboardButton("« Back", callback_data=back_cb)])
            msg_text = f"❌ দুঃখিত, **{category} ➡️ {sub_title}** এ বর্তমানে কোনো কোর্স পাওয়া যায়নি।"
        else:
            for course in courses:
                price_tag = f" ({course['price']} ৳)" if course.get('price', 0) > 0 else " 🆓"
                is_bought = " ✅" if db.is_purchased(user_id, course["id"]) else ""
                status_tag = "🔴 " if (course.get("status") == "inactive" and is_admin(user_id)) else ""
                keyboard.append([
                    InlineKeyboardButton(
                        f"{status_tag}{course['name']}{price_tag}{is_bought}",
                        callback_data=f"course_{course['id']}"
                    )
                ])
            parent_segs = subcat.split(" > ") if subcat != "ALL" else []
            back_cb = f"subcat_{category}_{' > '.join(parent_segs[:-1])}" if len(parent_segs) > 1 else f"cat_{category}"
            keyboard.append([InlineKeyboardButton("« Back", callback_data=back_cb)])
            msg_text = f"📚 **{category} | {sub_title} এর কোর্সসমূহ:**\n━━━━━━━━━━━━━━━━━━━━\nযে কোর্সের তথ্য দেখতে চান সেটিতে ক্লিক করুন:"

        if is_admin(user_id):
            if subcat != "ALL":
                nested_path = f"{category} > {subcat}"
                keyboard.append([
                    InlineKeyboardButton(f"➕ Add Course in '{subcat}'", callback_data=f"adm_addcourse_dir_{nested_path}"),
                    InlineKeyboardButton("📁 Manage Folder", callback_data=f"adm_dir_{nested_path}")
                ])
            else:
                keyboard.append([
                    InlineKeyboardButton(f"➕ Add Course to '{category}'", callback_data=f"adm_addcourse_cat_{category}")
                ])

        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(
                msg_text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.edit_message_text(
                msg_text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif data.startswith("course_"):
        course_id = data.replace("course_", "")
        course = db.get_course(course_id)
        if not course:
            await query.answer("❌ কোর্সটি খুঁজে পাওয়া যায়নি।", show_alert=True)
            return
        context.user_data["current_course"] = course_id
        await send_course_details(query, context, course, user_id, is_edit=True)

    elif data.startswith("addcart_"):
        course_id = data.replace("addcart_", "")
        course = db.get_course(course_id)
        if course:
            added = db.add_to_cart(user_id, course_id)
            if added:
                await query.answer(f"✅ '{course['name']}' কার্টে যুক্ত করা হয়েছে!", show_alert=True)
            else:
                await query.answer("ℹ️ কোর্সটি ইতিমধ্যে আপনার কার্টে রয়েছে!", show_alert=True)

    elif data.startswith("remcart_"):
        course_id = data.replace("remcart_", "")
        db.remove_from_cart(user_id, course_id)
        await query.answer("✕ আইটেম মুছে ফেলা হয়েছে!", show_alert=True)
        await show_cart(update, context)

    elif data in ("view_cart", "show_cart", "cart"):
        await show_cart(update, context)
        return

    elif data == "clear_cart":
        db.clear_cart(user_id)
        context.user_data.pop("applied_coupon", None)
        await query.answer("✕ কার্ট খালি করা হয়েছে!", show_alert=True)
        await show_cart(update, context)

    elif data == "checkout_cart":
        items = db.get_cart(user_id)
        if not items:
            await query.answer("কার্ট খালি!", show_alert=True)
            return

        total = sum(item.get("price", 0) for item in items)
        original_total = total
        course_names = ", ".join([i['name'] for i in items])
        course_ids = [i['id'] for i in items]

        context.user_data["checkout_type"] = "cart"
        context.user_data["checkout_courses"] = course_ids
        context.user_data["checkout_course_name"] = course_names

        applied_coupon = context.user_data.get("applied_coupon")
        discount_val = 0
        if applied_coupon:
            valid, discount, message, coupon_obj = db.validate_coupon_advanced(
                applied_coupon, user_id, total, "All", course_ids=course_ids
            )
            if valid:
                discount_val = discount
                total = max(0, total - discount)
        
        context.user_data["checkout_total"] = total

        user_bal = (db.get_user(user_id) or {}).get("balance", 0)
        methods = db.get_payment_methods(active_only=True)
        keyboard = []

        if user_bal >= total and total > 0:
            keyboard.append([
                InlineKeyboardButton(
                    f"💰 Pay with Wallet Balance (৳{user_bal})",
                    callback_data="pay_wallet_balance"
                )
            ])

        for method in methods:
            m_key = method['key'].lower()
            emoji = "💗" if "bkash" in m_key else "🟠" if "nagad" in m_key else "🟣" if "rocket" in m_key else "💳"
            keyboard.append([
                InlineKeyboardButton(
                    f"{emoji} {method['name']}",
                    callback_data=f"pay_{method['key']}"
                )
            ])

        if not applied_coupon:
            keyboard.append([InlineKeyboardButton("🎟 Apply Coupon", callback_data="coupon_cart")])
        else:
            keyboard.append([InlineKeyboardButton(f"🎟 Coupon: {applied_coupon} (Applied ✅)", callback_data="coupon_cart")])

        keyboard.append([InlineKeyboardButton("◀️ Back", callback_data="cancel_buy")])

        items_list = "\n".join([f"•  <b>{html.escape(i['name'])}</b> — ৳{i['price']}" for i in items])
        bal_info = f"\n💰 <b>Wallet Balance:</b> ৳{user_bal} BDT" if user_bal > 0 else ""

        if discount_val > 0:
            msg = f"""💳 <b>Select payment method:</b>

📦 <b>Cart Items ({len(items)} Courses):</b>
{items_list}

💰 <b>Price:</b> ৳{original_total}
🎟 <b>Coupon Discount:</b> -৳{discount_val}
✅ <b>Total:</b> ৳{total}{bal_info}"""
        else:
            msg = f"""💳 <b>Select payment method:</b>

📦 <b>Cart Items ({len(items)} Courses):</b>
{items_list}

💰 <b>Total:</b> ৳{total}{bal_info}"""

        course_image = items[0].get("image") if items else None
        await send_rich_course_message(query, msg, InlineKeyboardMarkup(keyboard), image=course_image, is_edit=True)

    elif data.startswith("free_enroll_"):
        course_id = data.replace("free_enroll_", "")
        course = db.get_course(course_id)
        if course:
            db.add_purchase(user_id, course_id)
            access_link = course.get("access_link", "")
            if access_link:
                db.store_user_access_link(user_id, course_id, access_link)
            keyboard = []
            if access_link:
                dyn_link = await get_dynamic_access_link(context.bot, access_link, user_id)
                if dyn_link:
                    keyboard.append([InlineKeyboardButton(" Go to Course", url=dyn_link)])
            keyboard.append([InlineKeyboardButton("🎓 My Courses", callback_data="my_courses_nav")])

            msg = f"""🎉 ** Congratulations! Course added to your profile!**

 **Course:** {course['name']}

👇 Click the button below to join:"""

            if query.message.photo:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("coupon_"):
        course_id = data.replace("coupon_", "")
        context.user_data["awaiting_coupon"] = True
        context.user_data["coupon_course"] = course_id

        if course_id == "cart":
            items = db.get_cart(user_id)
            if not items:
                await query.answer("❌ আপনার কার্ট খালি!", show_alert=True)
                return
            total = sum(item.get("price", 0) for item in items)
            context.user_data["checkout_courses"] = [i['id'] for i in items]
            context.user_data["checkout_total"] = total
            text = """🎟 **Coupon Code**
━━━━━━━━━━━━━━━━━━━━

Enter coupon code to get discount on your cart."""
            keyboard = [[InlineKeyboardButton("◀️ Back to Cart", callback_data="view_cart")]]
        else:
            text = """🎟 **Coupon Code**

Enter your code to claim your discount."""
            keyboard = [[InlineKeyboardButton("◀️ Back to Course", callback_data=f"course_{course_id}")]]

        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "browse_ebooks":
        await browse_ebooks(update, context)
        return

    elif data.startswith("ebcat_"):
        cat = data.replace("ebcat_", "")
        await show_ebooks_by_category(update, context, cat)
        return

    elif data.startswith("view_eb_"):
        eb_id = data.replace("view_eb_", "")
        await view_ebook_details(update, context, eb_id)
        return

    elif data.startswith("unlock_free_eb_"):
        eb_id = data.replace("unlock_free_eb_", "")
        db.add_ebook_purchase(user_id, eb_id)
        await query.answer("🎉 ই-বুকটি আপনার সংগৃহীত তালিকায় যুক্ত হয়েছে!", show_alert=True)
        await view_ebook_details(update, context, eb_id)
        return

    elif data.startswith("buy_eb_"):
        eb_id = data.replace("buy_eb_", "")
        eb = db.get_ebook(eb_id)
        if not eb:
            return
            
        total = eb["price"]
        context.user_data["checkout_type"] = "ebook"
        context.user_data["checkout_course"] = eb_id
        context.user_data["checkout_course_name"] = eb["name"]
        context.user_data["checkout_total"] = total
        
        user_bal = (db.get_user(user_id) or {}).get("balance", 0)
        methods = db.get_payment_methods(active_only=True)
        keyboard = []

        if user_bal >= total and total > 0:
            keyboard.append([
                InlineKeyboardButton(
                    f"💰 Pay with Wallet Balance (৳{user_bal})",
                    callback_data="pay_wallet_balance"
                )
            ])

        for method in methods:
            m_key = method['key'].lower()
            emoji = "💗" if "bkash" in m_key else "🟠" if "nagad" in m_key else "🟣" if "rocket" in m_key else "💳"
            keyboard.append([
                InlineKeyboardButton(
                    f"{emoji} {method['name']}",
                    callback_data=f"pay_{method['key']}"
                )
            ])
        keyboard.append([InlineKeyboardButton("◀️ Back", callback_data=f"view_eb_{eb_id}")])
        
        bal_info = f"\n💰 <b>Wallet Balance:</b> ৳{user_bal} BDT" if user_bal > 0 else ""
        msg = f"""💳 <b>Select payment method:</b>

📘 <b>E-Book:</b> {eb['name']}
💰 <b>Price:</b> ৳{eb['price']}
✅ <b>Total:</b> ৳{eb['price']}{bal_info}"""

        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data.startswith("buy_"):
        course_id = data.replace("buy_", "")
        course = db.get_course(course_id)
        if not course:
            return

        total = context.user_data.get("discounted_price") if context.user_data.get("coupon_course") == course_id else course["price"]
        context.user_data["checkout_type"] = "single"
        context.user_data["checkout_course"] = course_id
        context.user_data["checkout_course_name"] = course["name"]
        context.user_data["checkout_total"] = total

        user_bal = (db.get_user(user_id) or {}).get("balance", 0)
        methods = db.get_payment_methods(active_only=True)
        keyboard = []

        if user_bal >= total and total > 0:
            keyboard.append([
                InlineKeyboardButton(
                    f"💰 Pay with Wallet Balance (৳{user_bal})",
                    callback_data="pay_wallet_balance"
                )
            ])

        for method in methods:
            m_key = method['key'].lower()
            emoji = "💗" if "bkash" in m_key else "🟠" if "nagad" in m_key else "🟣" if "rocket" in m_key else "💳"
            keyboard.append([
                InlineKeyboardButton(
                    f"{emoji} {method['name']}",
                    callback_data=f"pay_{method['key']}"
                )
            ])
        keyboard.append([InlineKeyboardButton("◀️ Back", callback_data="cancel_buy")])

        bal_info = f"\n💰 <b>Wallet Balance:</b> ৳{user_bal} BDT" if user_bal > 0 else ""
        is_coupon_active = (context.user_data.get("coupon_course") == course_id)
        if is_coupon_active:
            discount_val = course["price"] - total
            msg = f"""💳 <b>Select payment method:</b>

📘 <b>Course:</b> {course['name']}
💰 <b>Price:</b> ৳{course['price']}
🎟 <b>Coupon Discount:</b> -৳{discount_val}
✅ <b>Total:</b> ৳{total}{bal_info}"""
        else:
            msg = f"""💳 <b>Select payment method:</b>

📘 <b>Course:</b> {course['name']}
💰 <b>Price:</b> ৳{course['price']}
✅ <b>Total:</b> ৳{total}{bal_info}"""

        course_image = course.get("image")
        await send_rich_course_message(query, msg, InlineKeyboardMarkup(keyboard), image=course_image, is_edit=True)

    elif data == "pay_wallet_balance":
        user_data = db.get_user(user_id) or {}
        user_bal = user_data.get("balance", 0)
        total = context.user_data.get("checkout_total", 0)
        c_type = context.user_data.get("checkout_type", "single")
        applied_coupon = context.user_data.get("applied_coupon", "")

        if user_bal < total:
            await query.answer("❌ আপনার ওয়ালেটে পর্যাপ্ত ব্যালেন্স নেই!", show_alert=True)
            return

        # Deduct balance
        deducted = db.deduct_balance(user_id, total)
        if not deducted:
            await query.answer("❌ ব্যালেন্স কাটতে সমস্যা হয়েছে! পুনরায় চেষ্টা করুন।", show_alert=True)
            return

        order_id = db.generate_next_order_id()
        course_name = context.user_data.get("checkout_course_name", "Course")
        course_id = context.user_data.get("checkout_course", "")
        courses_in_order = context.user_data.get("checkout_courses", [course_id] if course_id else [])

        order_data = {
            "user_id": user_id,
            "username": update.effective_user.username or "",
            "full_name": update.effective_user.full_name or "",
            "course_id": course_id,
            "courses": courses_in_order,
            "course_name": course_name,
            "amount": total,
            "payment_method": "Wallet Balance",
            "trxid": f"WALLET-{user_id}",
            "coupon_code": applied_coupon,
            "status": "approved",
            "date": str(datetime.now()),
            "checkout_type": c_type
        }
        db.add_order(order_id, order_data)

        if applied_coupon:
            db.use_coupon(applied_coupon, user_id)

        keyboard = []

        if c_type == "ebook":
            db.add_ebook_purchase(user_id, course_id)
            eb_obj = db.get_ebook(course_id)
            if eb_obj:
                if eb_obj.get("file_id"):
                    keyboard.append([InlineKeyboardButton("📥 Download (PDF)", callback_data=f"ebdl_{course_id}")])
                elif eb_obj.get("access_link"):
                    keyboard.append([InlineKeyboardButton("📥 Download E-Book", url=eb_obj["access_link"])])
            keyboard.append([InlineKeyboardButton("📚 My E-Books", callback_data="my_ebooks_nav")])
        elif c_type == "cart":
            for cid in courses_in_order:
                db.add_purchase(user_id, cid)
                c_obj = db.get_course(cid)
                if c_obj and c_obj.get("access_link"):
                    db.store_user_access_link(user_id, cid, c_obj["access_link"])
                    dyn_link = await get_dynamic_access_link(context.bot, c_obj["access_link"], user_id)
                    if dyn_link:
                        keyboard.append([InlineKeyboardButton(f"{c_obj['name']}", url=dyn_link)])
            db.clear_cart(user_id)
            keyboard.append([InlineKeyboardButton("🎓 My Courses", callback_data="my_courses_nav")])
        else:
            db.add_purchase(user_id, course_id)
            c_obj = db.get_course(course_id)
            if c_obj and c_obj.get("access_link"):
                db.store_user_access_link(user_id, course_id, c_obj["access_link"])
                dyn_link = await get_dynamic_access_link(context.bot, c_obj["access_link"], user_id)
                if dyn_link:
                    keyboard.append([InlineKeyboardButton(f"{c_obj['name']}", url=dyn_link)])
            keyboard.append([InlineKeyboardButton("🎓 My Courses", callback_data="my_courses_nav")])

        if db.is_home_button_enabled():
            keyboard.append([InlineKeyboardButton("⚜️ HOME", callback_data="back_to_main_menu")])

        # Clean context
        context.user_data.pop("discounted_price", None)
        context.user_data.pop("pay_method", None)
        context.user_data.pop("pay_amount", None)
        context.user_data.pop("checkout_course", None)
        context.user_data.pop("checkout_courses", None)
        context.user_data.pop("applied_coupon", None)
        context.user_data.pop("checkout_total", None)

        remaining_bal = (db.get_user(user_id) or {}).get("balance", 0)

        success_msg = f"""🎉 <b>পেমেন্ট সফল হয়েছে! (Wallet Balance)</b>
━━━━━━━━━━━━━━━━━━━━

📦 <b>Order ID:</b> <code>{format_order_id_display(order_id)}</code>
📘 <b>আইটেম:</b> {html.escape(course_name)}
💰 <b>পরিশোধিত মূল্য:</b> <code>৳{total} BDT</code>
💳 <b>অবশিষ্ট ওয়ালেট ব্যালেন্স:</b> <code>৳{remaining_bal} BDT</code>

✅ <i>আপনার কোর্স/ই-বুক সরাসরি সক্রিয় করা হয়েছে। নিচের বাটনে চাপ দিয়ে ক্লাসে যুক্ত হোন!</i>"""

        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(success_msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text(success_msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

        # Notify admins
        for aid in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    aid,
                    f"""🔔 <b>New Wallet Purchase Order!</b>\n━━━━━━━━━━━━━━━━━━━━\n📦 <b>Order:</b> <code>{format_order_id_display(order_id)}</code>\n👤 <b>User:</b> {html.escape(update.effective_user.full_name or 'User')} (<code>{user_id}</code>)\n📘 <b>Item:</b> {html.escape(course_name)}\n💰 <b>Amount:</b> <code>৳{total} BDT</code> (Wallet Auto-Approved)""",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        return

    elif data.startswith("pay_"):
        method_key = data.replace("pay_", "")
        total = context.user_data.get("checkout_total", 0)
        c_type = context.user_data.get("checkout_type")
        c_name = context.user_data.get("checkout_course_name")

        if not c_type:
            if context.user_data.get("checkout_courses") or context.user_data.get("coupon_course") == "cart":
                c_type = "cart"
                context.user_data["checkout_type"] = "cart"
            elif context.user_data.get("checkout_course") or context.user_data.get("current_course") or context.user_data.get("coupon_course"):
                c_type = "single"
                context.user_data["checkout_type"] = "single"

        if c_type == "cart":
            items = db.get_cart(user_id)
            if items:
                c_name = "\n" + "\n".join([f"  • {html.escape(i['name'])}" for i in items])
                context.user_data["checkout_courses"] = [i['id'] for i in items]
                context.user_data["checkout_course_name"] = ", ".join([i['name'] for i in items])
            elif not c_name:
                c_name = "Cart Items"
        else:
            cid = context.user_data.get("checkout_course") or context.user_data.get("current_course") or context.user_data.get("coupon_course")
            if cid and cid != "cart":
                c_obj = db.get_course(cid)
                if c_obj:
                    c_name = html.escape(c_obj.get("name", "Course"))
                    context.user_data["checkout_course"] = cid
                    context.user_data["checkout_course_name"] = c_obj.get("name", "Course")
            if not c_name or c_name == "Course":
                items = db.get_cart(user_id)
                if items:
                    c_type = "cart"
                    context.user_data["checkout_type"] = "cart"
                    c_name = "\n" + "\n".join([f"  • {html.escape(i['name'])}" for i in items])

        if not c_name:
            c_name = "Course"

        method_obj = db.get_payment_method(method_key)
        method_name = method_obj["name"] if method_obj else method_key.title()
        method_number = method_obj["number"] if method_obj else "01XXXXXXXXX"
        method_ins = method_obj.get("instruction", "Personal (Send Money)") if method_obj else "Personal"
        payment_note = db.get_payment_note()

        context.user_data["awaiting_trxid"] = True
        context.user_data["pay_method"] = method_name
        context.user_data["pay_amount"] = total

        method_emoji = "💗" if "bkash" in method_key.lower() else "🟠" if "nagad" in method_key.lower() else "🟣" if "rocket" in method_key.lower() else "💳"
        course_label = "Courses" if c_type == "cart" else "Course"
        instructions = f"""{method_emoji} <b>{method_name} Payment Instruction</b>
━━━━━━━━━━━━━━━━━━━━━
📘 <b>{course_label}:</b> {c_name}
📱 <b>Number:</b> <code>{method_number}</code>
💰 <b>Amount:</b> ৳<code>{total}</code>

💡 {method_ins}

{payment_note}"""

        keyboard = [
            
            [InlineKeyboardButton("◀️ Back", callback_data="cancel_trxid")]
        ]

        course_image = None
        c_type = context.user_data.get("checkout_type")
        if c_type == "single":
            course_id = context.user_data.get("checkout_course")
            course = db.get_course(course_id)
            if course:
                course_image = course.get("image")
        elif c_type == "cart":
            cart_courses = context.user_data.get("checkout_courses", [])
            if cart_courses:
                first_course = db.get_course(cart_courses[0])
                if first_course:
                    course_image = first_course.get("image")

        await send_rich_course_message(query, instructions, InlineKeyboardMarkup(keyboard), image=course_image, is_edit=True)

    elif data.startswith("copy_num_"):
        num = data.replace("copy_num_", "")
        await query.answer()
        return

    elif data.startswith("share_"):
        course_id = data.replace("share_", "")
        course = db.get_course(course_id)
        if course:
            share_text = f"🎓 {course['name']}\n💰 মূল্য: {course['price']} ৳\n\nএখনই জয়েন করতে টেলিগ্রাম বটের এই লিংকে ক্লিক করুন:\nhttps://t.me/{BOT_USERNAME}?start=course_{course_id}"
            share_url = f"https://t.me/share/url?url={quote_plus(f'https://t.me/{BOT_USERNAME}?start=course_{course_id}')}&text={quote_plus(course['name'])}"
            await query.message.reply_text(
                share_text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➥ Share Link", url=share_url)]])
            )

    elif data == "my_orders_history":
        orders = db.get_user_orders(user_id)
        if not orders:
            await query.edit_message_text("🧾 No order history found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Profile", callback_data="profile_nav")]]))
            return
        msg = "🧾 <b>Order History:</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        for o in orders[:8]:
            status_en = "⏳ Pending" if o['status'] == 'pending' else "✅ Approved" if o['status'] == 'approved' else "❌ Rejected"
            c_name_esc = html.escape(str(o.get('course_name', 'N/A')))
            pay_m_esc = html.escape(str(o.get('payment_method', '')))
            pay_info = f" | {pay_m_esc}" if pay_m_esc else ""
            msg += f"• <b>ID:</b> <code>{format_order_id_display(o['order_id'])}</code>\n  <b>Course:</b> {c_name_esc}\n  💰 <b>Amount:</b> ৳{o['amount']} BDT{pay_info}\n  📌 <b>Status:</b> {status_en}\n\n"
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Profile", callback_data="profile_nav")]]))

    elif data == "cancel_buy" or data == "cancel_trxid":
        context.user_data["awaiting_trxid"] = False
        context.user_data.pop("pay_method", None)
        context.user_data.pop("pay_amount", None)
        context.user_data.pop("applied_coupon", None)
        context.user_data.pop("discounted_price", None)
        c_type = context.user_data.pop("checkout_type", None)

        if c_type == "single" and context.user_data.get("checkout_course"):
            course_id = context.user_data.pop("checkout_course")
            course = db.get_course(course_id)
            if course:
                await send_course_details(query, context, course, user_id, is_edit=True)
                return
        elif c_type == "cart":
            await show_cart(update, context)
            return

        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass

        raw_welcome = db.get_setting("welcome_message", WELCOME_MESSAGE)
        cleaned_welcome, custom_kb = parse_inline_buttons(raw_welcome)
        
        if custom_kb:
            await query.message.reply_text(
                cleaned_welcome,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(custom_kb)
            )
        else:
            await query.message.reply_text(
                raw_welcome,
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard(user_id)
            )
            welcome_kb = get_welcome_inline_keyboard()
            if welcome_kb and welcome_kb.inline_keyboard:
                await query.message.reply_text(
                    "",
                    reply_markup=welcome_kb
                )

    elif data.startswith("ebdl_"):
        eb_id = data.replace("ebdl_", "")
        eb = db.get_ebook(eb_id)
        if eb and eb.get("file_id"):
            try:
                await query.answer("📥 ডাউনলোড শুরু হচ্ছে...")
                await context.bot.send_document(
                    chat_id=user_id,
                    document=eb["file_id"],
                    caption=f"📖 **{eb['name']}**\n\n🎓",
                    parse_mode="Markdown"
                )
            except Exception as e:
                await query.answer("❌ ফাইল পাঠাতে সমস্যা হয়েছে!", show_alert=True)
        else:
            await query.answer("ফাইল পাওয়া যায়নি!", show_alert=True)

    elif data.startswith(("admin_", "adm_", "approve_", "reject_", "ordinfo_", "edprop_", "edebprop_", "apprwdr_", "rejwdr_", "wdrinfo_", "cpnwiz_", "bc_sel_")):
        await handle_admin_callback(update, context)


async def handle_admin_kb_add_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
        
    text = update.message.text.strip()
    if text == "/cancel":
        context.user_data.pop("admin_add_kb_step", None)
        context.user_data.pop("admin_add_kb_data", None)
        await update.message.reply_text("✕ Keyboard configuration aborted.", reply_markup=main_menu_keyboard(user_id))
        return
        
    step = context.user_data.get("admin_add_kb_step")
    
    if step == "text":
        context.user_data["admin_add_kb_data"]["text"] = text
        await update.message.reply_text(
            f"Button text saved: `{text}`\n\n👇 **Select the action type for this button when clicked:**",
            parse_mode="Markdown",
            reply_markup=get_action_select_keyboard(is_edit=False)
        )
        context.user_data.pop("admin_add_kb_step", None)
        
    elif step == "custom_action":
        context.user_data["admin_add_kb_data"]["action"] = text
        context.user_data.pop("admin_add_kb_step", None)
        
        custom_kb = db.get_custom_keyboards()
        keyboard = []
        buttons_grid = custom_kb.get("buttons", [])
        for r_idx in range(len(buttons_grid)):
            keyboard.append([InlineKeyboardButton(f"Row {r_idx + 1}", callback_data=f"adm_kb_row_{r_idx}")])
        keyboard.append([InlineKeyboardButton("➕ New Row", callback_data="adm_kb_row_new")])
        keyboard.append([InlineKeyboardButton("« Cancel", callback_data="adm_keyboard_settings")])
        
        await update.message.reply_text(
            f"Custom callback data saved: `{text}`\n\n👇 **Choose which row to place the new button on:**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_admin_kb_edit_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
        
    text = update.message.text.strip()
    coords = context.user_data.get("admin_edit_kb_coords")
    if not coords:
        context.user_data.pop("admin_edit_kb_step", None)
        await update.message.reply_text("✕ Editing session expired.", reply_markup=main_menu_keyboard(user_id))
        return
        
    r_idx, c_idx = coords
    
    if text == "/cancel":
        context.user_data.pop("admin_edit_kb_step", None)
        custom_kb = db.get_custom_keyboards()
        btn = custom_kb["buttons"][r_idx][c_idx]
        adm_marker = "🟢 Yes (Admins Only)" if btn.get("admin_only") else "🔴 No (All Users)"
        msg = f"""✏️ **Modify Keyboard Button**
━━━━━━━━━━━━━━━━━━━━
• **Text:** `{btn['text']}`
• **Action:** `{btn.get('action')}`
• **Admin Only:** {adm_marker}
• **Position:** Row {r_idx+1}, Column {c_idx+1}

Choose what you want to edit:"""
        keyboard = [
            [InlineKeyboardButton("✏️ Edit Text", callback_data="adm_kb_edt_text"), InlineKeyboardButton("🔗 Edit Action", callback_data="adm_kb_edt_action")],
            [InlineKeyboardButton("↔️ Move Position", callback_data="adm_kb_edt_move"), InlineKeyboardButton("👑 Toggle Admin-Only", callback_data="adm_kb_edt_adm")],
            [InlineKeyboardButton("« Keyboard Settings", callback_data="adm_keyboard_settings")]
        ]
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return
        
    step = context.user_data.get("admin_edit_kb_step")
    custom_kb = db.get_custom_keyboards()
    
    if step == "text":
        custom_kb["buttons"][r_idx][c_idx]["text"] = text
        db.save_custom_keyboards(custom_kb)
        context.user_data.pop("admin_edit_kb_step", None)
        
        await update.message.reply_text("✅ Button text updated successfully!")
        
        btn = custom_kb["buttons"][r_idx][c_idx]
        adm_marker = "🟢 Yes (Admins Only)" if btn.get("admin_only") else "🔴 No (All Users)"
        msg = f"""✏️ **Modify Keyboard Button**
━━━━━━━━━━━━━━━━━━━━
• **Text:** `{btn['text']}`
• **Action:** `{btn.get('action')}`
• **Admin Only:** {adm_marker}
• **Position:** Row {r_idx+1}, Column {c_idx+1}

Choose what you want to edit:"""
        keyboard = [
            [InlineKeyboardButton("✏️ Edit Text", callback_data="adm_kb_edt_text"), InlineKeyboardButton("🔗 Edit Action", callback_data="adm_kb_edt_action")],
            [InlineKeyboardButton("↔️ Move Position", callback_data="adm_kb_edt_move"), InlineKeyboardButton("👑 Toggle Admin-Only", callback_data="adm_kb_edt_adm")],
            [InlineKeyboardButton("« Keyboard Settings", callback_data="adm_keyboard_settings")]
        ]
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        
    elif step == "custom_action":
        custom_kb["buttons"][r_idx][c_idx]["action"] = text
        db.save_custom_keyboards(custom_kb)
        context.user_data.pop("admin_edit_kb_step", None)
        
        await update.message.reply_text("✅ Button action updated successfully!")
        
        btn = custom_kb["buttons"][r_idx][c_idx]
        adm_marker = "🟢 Yes (Admins Only)" if btn.get("admin_only") else "🔴 No (All Users)"
        msg = f"""✏️ **Modify Keyboard Button**
━━━━━━━━━━━━━━━━━━━━
• **Text:** `{btn['text']}`
• **Action:** `{btn.get('action')}`
• **Admin Only:** {adm_marker}
• **Position:** Row {r_idx+1}, Column {c_idx+1}

Choose what you want to edit:"""
        keyboard = [
            [InlineKeyboardButton("✏️ Edit Text", callback_data="adm_kb_edt_text"), InlineKeyboardButton("🔗 Edit Action", callback_data="adm_kb_edt_action")],
            [InlineKeyboardButton("↔️ Move Position", callback_data="adm_kb_edt_move"), InlineKeyboardButton("👑 Toggle Admin-Only", callback_data="adm_kb_edt_adm")],
            [InlineKeyboardButton("« Keyboard Settings", callback_data="adm_keyboard_settings")]
        ]
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== USER MESSAGE & SEARCH HANDLER ====================

async def handle_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    under_maint, maint_msg = check_maintenance(user.id)
    if under_maint:
        if update.message:
            await update.message.reply_text(maint_msg, parse_mode="Markdown")
        return

    msg = update.message
    text = msg.text.strip() if msg and msg.text else ""
    caption = msg.caption.strip() if msg and msg.caption else ""
    user_id = update.effective_user.id

    # Universal Cancel check
    check_str = (text or caption).strip().lower()
    if check_str in ["/cancel", "cancel", "বাতিল", "❌ cancel", "abort", "/stop"]:
        await cancel_cmd(update, context)
        return

    # Cancel any active wizard if a menu command or button is pressed
    if text:
        is_command = text.startswith("/")
        is_kb_button = False
        
        # Check standard navigation buttons
        standard_buttons = ["🛍️ Cart", "👤 Profile", "ℹ Info", "⚙ Admin Panel", "« Back", "⚜️ HOME"]
        if text in standard_buttons:
            is_kb_button = True
            
        # Check custom keyboards
        if not is_kb_button:
            try:
                custom_kb = db.get_custom_keyboards()
                for row in custom_kb.get("buttons", []):
                    for btn in row:
                        if btn.get("text") == text:
                            is_kb_button = True
                            break
                    if is_kb_button:
                        break
            except Exception:
                pass
                
        if is_command or is_kb_button:
            # Clear all pending input states
            pending_keys = [
                "admin_add_kb_step", "admin_edit_kb_step", "admin_add_course",
                "admin_add_ebook", "admin_edit_ebook_field", "admin_edit_field",
                "admin_add_category", "admin_add_subcat", "awaiting_folder_rename",
                "admin_add_coupon_wizard", "admin_pay_step", "admin_msg_edit_key",
                "admin_user_step", "admin_broadcasting_step", "withdraw_step",
                "awaiting_coupon", "awaiting_trxid", "admin_order_search_step"
            ]
            for key in pending_keys:
                context.user_data.pop(key, None)

    # Admin workflows
    if context.user_data.get("admin_add_kb_step"):
        await handle_admin_kb_add_input(update, context)
        return

    if context.user_data.get("admin_edit_kb_step"):
        await handle_admin_kb_edit_input(update, context)
        return

    if context.user_data.get("admin_add_course"):
        await handle_admin_course_creation(update, context)
        return

    if context.user_data.get("admin_add_ebook"):
        await handle_admin_ebook_creation(update, context)
        return

    if context.user_data.get("admin_edit_ebook_field"):
        await handle_admin_ebook_edit_input(update, context)
        return

    if context.user_data.get("admin_edit_field"):
        await handle_admin_course_edit_input(update, context)
        return

    if context.user_data.get("admin_add_category"):
        await handle_admin_category_creation(update, context)
        return

    if context.user_data.get("admin_add_subcat"):
        await handle_admin_subcategory_creation(update, context)
        return

    if context.user_data.get("admin_add_eb_subcat"):
        await handle_admin_ebook_subcategory_creation(update, context)
        return

    if context.user_data.get("awaiting_folder_rename"):
        text = update.message.text.strip()
        if text == "/cancel":
            context.user_data.pop("awaiting_folder_rename", None)
            context.user_data.pop("rename_folder_old", None)
            context.user_data.pop("rename_folder_parent", None)
            await update.message.reply_text("✕ Rename cancelled.", reply_markup=main_menu_keyboard(user_id))
            return
        old_name = context.user_data.get("rename_folder_old", "")
        parent_path = context.user_data.get("rename_folder_parent", "")
        success = db.rename_folder(parent_path, old_name, text)
        context.user_data.pop("awaiting_folder_rename", None)
        context.user_data.pop("rename_folder_old", None)
        context.user_data.pop("rename_folder_parent", None)
        if success:
            active_dir = parent_path + " > " + text if parent_path else text
            await update.message.reply_text(f"✅ Renamed to '{text}'!", reply_markup=main_menu_keyboard(user_id))
            from telegram import Update as Upd, InlineKeyboardMarkup as IKM
            class FakeQ:
                def __init__(self, msg): self.message = msg; self.data = "adm_dir_" + active_dir; self.from_user = update.effective_user; self.id = "0"
                async def answer(self, *a, **kw): pass
                async def edit_message_text(self, *a, **kw): return await self.message.reply_text(*a, **kw)
            await render_admin_folder_directory(FakeQ(update.message), context, active_dir)
        else:
            await update.message.reply_text("❌ Rename failed. Name may already exist.", reply_markup=main_menu_keyboard(user_id))
        return

    if context.user_data.get("awaiting_eb_folder_rename"):
        text = update.message.text.strip()
        if text == "/cancel":
            context.user_data.pop("awaiting_eb_folder_rename", None)
            context.user_data.pop("rename_eb_folder_old", None)
            context.user_data.pop("rename_eb_folder_parent", None)
            await update.message.reply_text("✕ Rename cancelled.", reply_markup=main_menu_keyboard(user_id))
            return
        old_name = context.user_data.get("rename_eb_folder_old", "")
        parent_path = context.user_data.get("rename_eb_folder_parent", "")
        success = db.rename_ebook_folder(parent_path, old_name, text)
        context.user_data.pop("awaiting_eb_folder_rename", None)
        context.user_data.pop("rename_eb_folder_old", None)
        context.user_data.pop("rename_eb_folder_parent", None)
        if success:
            active_dir = parent_path + " > " + text if parent_path else text
            await update.message.reply_text(f"✅ Renamed to '{text}'!", reply_markup=main_menu_keyboard(user_id))
            class FakeEBQ:
                def __init__(self, msg): self.message = msg; self.data = "adm_ebdir_" + active_dir; self.from_user = update.effective_user; self.id = "0"
                async def answer(self, *a, **kw): pass
                async def edit_message_text(self, *a, **kw): return await self.message.reply_text(*a, **kw)
            await render_admin_ebook_folder_directory(FakeEBQ(update.message), context, active_dir)
        else:
            await update.message.reply_text("❌ Rename failed. Name may already exist.", reply_markup=main_menu_keyboard(user_id))
        return

    if context.user_data.get("admin_add_coupon_wizard"):
        await handle_admin_coupon_creation_wizard(update, context)
        return

    if context.user_data.get("admin_pay_step"):
        await handle_admin_payment_input(update, context)
        return

    if context.user_data.get("admin_edit_infoitem_key"):
        item_key = context.user_data.pop("admin_edit_infoitem_key")
        if text == "/cancel" or text.lower() == "cancel":
            back_kb = [[InlineKeyboardButton("« Back to Section", callback_data=f"adm_infoitem_{item_key}")]]
            await update.message.reply_text("✕ Editing cancelled.", reply_markup=InlineKeyboardMarkup(back_kb))
            return
        db.update_info_item(item_key, content=text)
        if item_key == "contact":
            db.set_setting("support_message", text)
        item_label = db.get_info_settings().get("items", {}).get(item_key, {}).get("label") or item_key
        keyboard = [
            [InlineKeyboardButton(f"🔍 View {item_label}", callback_data=f"adm_infoitem_{item_key}")],
            [InlineKeyboardButton("ℹ️ Info Button Settings", callback_data="adm_info_buttons")]
        ]
        await update.message.reply_text(
            f"✅ Description for '<b>{html.escape(item_label)}</b>' updated successfully!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if context.user_data.get("admin_msg_edit_key"):
        msg_key = context.user_data.pop("admin_msg_edit_key")
        if text == "/cancel" or text.lower() == "cancel":
            back_kb = [[InlineKeyboardButton("⚙️ Bot Settings", callback_data="adm_bot_settings")]]
            await update.message.reply_text("✕ Cancelled.", reply_markup=InlineKeyboardMarkup(back_kb))
            return
        db.set_setting(msg_key, text)
        
        # If it is bot_description, update on Telegram immediately
        if msg_key == "bot_description":
            try:
                await context.bot.set_my_description(description=text)
            except Exception as e:
                logger.error(f"Failed to update bot description on Telegram: {e}")
                
        display_key = msg_key.replace("_", " ").title()
        back_kb = [
            [InlineKeyboardButton("⚙️ Bot Settings", callback_data="adm_bot_settings")],
            [InlineKeyboardButton("« Admin Menu", callback_data="adm_main")]
        ]
        if msg_key.startswith("delivery_"):
            back_kb.insert(0, [InlineKeyboardButton("📦 Delivery Settings", callback_data="adm_editmsg_delivery_message")])

        await update.message.reply_text(
            f"✅ **{display_key} updated successfully!**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(back_kb)
        )
        return

    if context.user_data.get("admin_add_hbtn_step"):
        step = context.user_data.get("admin_add_hbtn_step")
        if text == "/cancel" or text.lower() == "cancel":
            context.user_data.pop("admin_add_hbtn_step", None)
            context.user_data.pop("admin_add_hbtn_data", None)
            await update.message.reply_text("✕ Add Button operation cancelled.", reply_markup=main_menu_keyboard(user_id))
            return

        if step == "text":
            context.user_data["admin_add_hbtn_data"]["text"] = text
            context.user_data["admin_add_hbtn_step"] = "action"
            await update.message.reply_text(
                "✍️ <b>Enter the action (URL link or callback data like my_courses_nav):</b>\n\nType /cancel to abort.",
                parse_mode="HTML"
            )
        elif step == "action":
            context.user_data["admin_add_hbtn_data"]["action"] = text
            
            class FakeCallbackQuery:
                def __init__(self, message):
                    self.message = message
                async def edit_message_text(self, text, parse_mode, reply_markup):
                    await self.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
            
            fake_query = FakeCallbackQuery(update.message)
            await prompt_add_hbtn_row(fake_query, context)
            
        elif step == "edit_text":
            coords = context.user_data.get("admin_edit_hbtn_coords")
            if coords:
                r_idx, c_idx = coords
                grid = get_home_keyboard_grid()
                if r_idx < len(grid) and c_idx < len(grid[r_idx]):
                    grid[r_idx][c_idx]["text"] = text
                    db.set_setting("home_keyboard_grid", grid)
                    context.user_data.pop("admin_add_hbtn_step", None)
                    await update.message.reply_text("✅ Button text updated successfully!")
                    
                    class FakeCallbackQuery:
                        def __init__(self, message):
                            self.message = message
                        async def edit_message_text(self, text, parse_mode, reply_markup):
                            await self.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
                            
                    fake_query = FakeCallbackQuery(update.message)
                    await render_hbtn_edit_panel(fake_query, context, r_idx, c_idx)
            
        elif step == "edit_action":
            coords = context.user_data.get("admin_edit_hbtn_coords")
            if coords:
                r_idx, c_idx = coords
                grid = get_home_keyboard_grid()
                if r_idx < len(grid) and c_idx < len(grid[r_idx]):
                    grid[r_idx][c_idx]["action"] = text
                    db.set_setting("home_keyboard_grid", grid)
                    context.user_data.pop("admin_add_hbtn_step", None)
                    await update.message.reply_text("✅ Button action updated successfully!")
                    
                    class FakeCallbackQuery:
                        def __init__(self, message):
                            self.message = message
                        async def edit_message_text(self, text, parse_mode, reply_markup):
                            await self.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
                            
                    fake_query = FakeCallbackQuery(update.message)
                    await render_hbtn_edit_panel(fake_query, context, r_idx, c_idx)
        return

    if context.user_data.get("admin_add_infobtn_step"):
        step = context.user_data.get("admin_add_infobtn_step")
        if text == "/cancel" or text.lower() == "cancel":
            context.user_data.pop("admin_add_infobtn_step", None)
            context.user_data.pop("admin_add_infobtn_data", None)
            await update.message.reply_text("✕ Add Info Button cancelled.", reply_markup=main_menu_keyboard(user_id))
            return

        if step == "label":
            if not context.user_data.get("admin_add_infobtn_data"):
                context.user_data["admin_add_infobtn_data"] = {}
            context.user_data["admin_add_infobtn_data"]["label"] = text
            context.user_data["admin_add_infobtn_step"] = "content"
            await update.message.reply_text(
                "✍️ <b>Enter the URL link or text content for this button:</b>\n\n• If you provide a link (e.g. <code>https://t.me/...</code>), it will open as a URL.\n• If you provide text, clicking the button will display your text.\n\nType /cancel to abort.",
                parse_mode="HTML"
            )
            return
        elif step == "content":
            btn_data = context.user_data.pop("admin_add_infobtn_data", {})
            context.user_data.pop("admin_add_infobtn_step", None)
            lbl = btn_data.get("label", "Info Button")
            b_type = "url" if (text.startswith("http://") or text.startswith("https://") or text.startswith("t.me")) else "text"
            db.add_custom_info_button(lbl, b_type, text)
            keyboard = [[InlineKeyboardButton("ℹ️ Info Button Settings", callback_data="adm_info_buttons")]]
            await update.message.reply_text(
                f"✅ Custom Info button '<b>{html.escape(lbl)}</b>' added successfully!",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

    if context.user_data.get("admin_user_step"):
        await handle_admin_user_mgmt_input(update, context)
        return

    if context.user_data.get("admin_order_search_step"):
        context.user_data.pop("admin_order_search_step", None)
        text_val = update.message.text.strip() if update.message.text else ""
        if text_val == "/cancel" or text_val.lower() == "cancel":
            await update.message.reply_text("✕ Aborted.", reply_markup=main_menu_keyboard(user_id))
            return
            
        q = text_val.lower()
        found_orders = []
        for oid, o in db.orders.items():
            if q in oid.lower() or q in str(o.get("trxid", "")).lower() or q in str(o.get("user_id", "")).lower() or q in str(o.get("full_name", "")).lower() or q in str(o.get("course_name", "")).lower():
                o_copy = dict(o)
                o_copy["order_id"] = oid
                found_orders.append(o_copy)
                
        if not found_orders:
            await update.message.reply_text(
                f"❌ No orders found matching '{text_val}'!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Pending Orders", callback_data="adm_pending_orders")]])
            )
            return
            
        keyboard = []
        for o in found_orders[:15]:
            status_dot = "⏳ " if o.get("status") == "pending" else "✅ " if o.get("status") == "approved" else "❌ "
            student_name = o.get('full_name') or o.get('username') or 'Student'
            keyboard.append([
                InlineKeyboardButton(
                    f"{status_dot}{format_order_id_display(o['order_id'])} — {student_name[:15]} — {o['amount']}৳",
                    callback_data=f"ordinfo_{o['order_id']}"
                )
            ])
        keyboard.append([InlineKeyboardButton("« Pending Orders", callback_data="adm_pending_orders")])
        
        await update.message.reply_text(
            f"🔍 **Search Results for '{text_val}' ({len(found_orders)} found):**\nClick to view details:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Broadcast Flow Handling
    if context.user_data.get("admin_broadcasting_step"):
        await handle_broadcast_input_step(update, context)
        return

    # User active workflows
    if context.user_data.get("withdraw_step"):
        await handle_withdraw_input(update, context)
        return

    if context.user_data.get("awaiting_coupon"):
        await handle_coupon_input(update, context)
        return

    if context.user_data.get("awaiting_trxid"):
        await handle_trxid_input(update, context)
        return

    if not text:
        return

    # Check for custom keyboard buttons
    custom_kb = db.get_custom_keyboards()
    matched_btn = None
    for row in custom_kb.get("buttons", []):
        for btn in row:
            if btn.get("text") == text:
                matched_btn = btn
                break
        if matched_btn:
            break

    if matched_btn:
        if matched_btn.get("admin_only") and not is_admin(user_id):
            await update.message.reply_text("⛔ Access Denied! You are not an admin.")
            return
        
        action = matched_btn.get("action", "")
        if action == "cart":
            await show_cart(update, context)
            return
        elif action == "profile":
            await show_profile(update, context)
            return
        elif action == "info":
            await show_info(update, context)
            return
        elif action == "admin":
            await admin_cmd(update, context)
            return
        else:
            class FakeCallbackQuery:
                def __init__(self, message, data, user):
                    self.message = message
                    self.data = data
                    self.from_user = user
                    self.id = "0"

                async def answer(self, text=None, show_alert=False):
                    pass

                async def edit_message_text(self, text, *args, **kwargs):
                    return await self.message.reply_text(text, *args, **kwargs)

            fake_query = FakeCallbackQuery(update.message, action, update.effective_user)

            class FakeUpdate:
                def __init__(self, message, callback_query):
                    self.message = message
                    self.callback_query = callback_query
                    self.effective_user = callback_query.from_user
                    self.effective_chat = message.chat
                    self.update_id = 0

            fake_update = FakeUpdate(update.message, fake_query)
            await handle_callback(fake_update, context)
            return

    # Clean matching for styled buttons
    clean_t = text.replace("⊞", "").replace("👤", "").replace("ℹ", "").replace("⚙", "").replace("🛒", "").replace("🛍️", "").replace("🛍", "").replace("+", "").replace("⚜️", "").replace("⚜", "").strip().lower()

    if clean_t in ["cart", "কার্ট", "my cart"]:
        await show_cart(update, context)
    elif clean_t in ["profile", "প্রোফাইল"]:
        await show_profile(update, context)
    elif clean_t in ["info", "তথ্য ও সাপোর্ট", "তথ্য", "help"]:
        await show_info(update, context)
    elif clean_t in ["admin panel", "admin"]:
        await admin_cmd(update, context)
    elif clean_t in ["home", "হোম", "main menu", "menu", "start"]:
        await start(update, context)
    elif clean_t in ["ssc"]:
        context.user_data["category"] = "SSC"
        await show_subcategories_message(update, "SSC")
    elif clean_t in ["hsc 28"]:
        context.user_data["category"] = "HSC 28"
        await show_subcategories_message(update, "HSC 28")
    elif clean_t in ["hsc 27"]:
        context.user_data["category"] = "HSC 27"
        await show_subcategories_message(update, "HSC 27")
    elif clean_t in ["hsc 26"]:
        context.user_data["category"] = "HSC 26"
        await show_subcategories_message(update, "HSC 26")
    elif clean_t in ["আমার কোর্সসমূহ", "my courses", "আমার কোর্স"]:
        await show_my_courses(update, context)
    else:
        # Automatic intelligent course search for any typed query (GeniusHub style)
        await auto_search_courses(update, text)


async def show_subcategories_message(update: Update, category: str):
    user_id = update.effective_user.id if update.effective_user else 0
    show_inactive = is_admin(user_id)
    subcats = db.get_subcategories(category, include_inactive=show_inactive)
    keyboard = []
    row = []
    for sub in subcats:
        sub_path = f"{category} > {sub}"
        is_sub_active = db.is_category_active(sub_path)
        tag = " [OFF 🔴]" if (not is_sub_active and show_inactive) else ""
        sub_courses = db.get_courses_by_filter(category=category, subcategory=sub, include_inactive=show_inactive)
        count = len(sub_courses)
        row.append(InlineKeyboardButton(f"{sub}{tag} ({count})", callback_data=f"subcat_{category}_{sub}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # Direct courses in root category (if any)
    direct_courses = db.get_courses_by_folder(category, include_inactive=show_inactive)
    for course in direct_courses:
        price_tag = f" ({course['price']} ৳)" if course.get('price', 0) > 0 else " 🆓"
        is_bought = " ✅" if db.is_purchased(user_id, course["id"]) else ""
        status_tag = "🔴 " if (course.get("status") == "inactive" and show_inactive) else ""
        keyboard.append([
            InlineKeyboardButton(
                f"{status_tag}{course['name']}{price_tag}{is_bought}",
                callback_data=f"course_{course['id']}"
            )
        ])

    if db.is_view_all_courses_enabled():
        all_c = db.get_courses_by_filter(category=category, include_inactive=show_inactive)
        keyboard.append([InlineKeyboardButton(f"🎓 View All {category} Courses ({len(all_c)})", callback_data=f"subcat_{category}_ALL")])
    keyboard.append([InlineKeyboardButton("« All Categories", callback_data="back_to_categories")])

    if subcats and direct_courses:
        text = f"📂 **{category} এর বিষয় ও কোর্সসমূহ:**\n━━━━━━━━━━━━━━━━━━━━\nআপনার প্রয়োজনীয় বিষয় বা কোর্স বেছে নিন:"
    elif direct_courses:
        text = f"📚 **{category} এর কোর্সসমূহ:**\n━━━━━━━━━━━━━━━━━━━━\nযে কোর্সের তথ্য দেখতে চান সেটিতে ক্লিক করুন:"
    else:
        text = f"📂 **{category} এর বিষয় / সাব-ক্যাটাগরি:**\n━━━━━━━━━━━━━━━━━━━━\nআপনার প্রয়োজনীয় বিষয় বা প্রোগ্রাম বেছে নিন:"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_coupon_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["awaiting_coupon"] = False
    user_id = update.effective_user.id

    if text == "/cancel" or text.lower() == "cancel":
        await update.message.reply_text("✕ Coupon application cancelled.", reply_markup=main_menu_keyboard(user_id))
        return

    course_id = context.user_data.get("coupon_course")
    if course_id == "cart":
        items = db.get_cart(user_id)
        if not items:
            await update.message.reply_text("❌ Your cart is empty.", reply_markup=main_menu_keyboard(user_id))
            return
        courses_in_order = [i['id'] for i in items]
        cart_total = sum(item.get("price", 0) for item in items)
        context.user_data["checkout_type"] = "cart"
        context.user_data["checkout_courses"] = courses_in_order
        context.user_data["checkout_course_name"] = ", ".join([i['name'] for i in items])
        context.user_data["checkout_total"] = cart_total

        valid, discount, message, coupon_obj = db.validate_coupon_advanced(
            text, user_id, cart_total, "All", course_ids=courses_in_order
        )
        if valid:
            new_total = max(0, cart_total - discount)
            context.user_data["checkout_total"] = new_total
            context.user_data["applied_coupon"] = text.upper()
            context.user_data["checkout_type"] = "cart"
            context.user_data["checkout_courses"] = courses_in_order
            context.user_data["checkout_course_name"] = ", ".join([i['name'] for i in items])

            if new_total == 0:
                courses_in_order = context.user_data.get("checkout_courses", [])
                for cid in courses_in_order:
                    db.add_purchase(user_id, cid)
                    c_obj = db.get_course(cid)
                    if c_obj and c_obj.get("access_link"):
                        db.store_user_access_link(user_id, cid, c_obj["access_link"])

                db.use_coupon(text.upper(), user_id)
                db.clear_cart(user_id)

                context.user_data.pop("discounted_price", None)
                context.user_data.pop("pay_method", None)
                context.user_data.pop("pay_amount", None)
                context.user_data.pop("checkout_course", None)
                context.user_data.pop("checkout_courses", None)
                context.user_data.pop("applied_coupon", None)

                keyboard = [[InlineKeyboardButton("🎓 My Courses", callback_data="my_courses_nav")]]
                await update.message.reply_text(
                    f"""🎉 <b>কুপন দিয়ে কার্টের সকল কোর্স সফলভাবে ফ্রি এনরোল হয়েছে!</b>
━━━━━━━━━━━━━━━━━━━━

🏷️ <b>কুপন:</b> <code>{text.upper()}</code>
📦 <b>কার্ট আইটেম:</b> {len(courses_in_order)}টি কোর্স
💰 <b>মোট মূল্য:</b> ৳{cart_total} → <b>ফ্রি!</b>

👇 ক্লাসে যুক্ত হতে এবং আপনার কোর্সগুলো দেখতে নিচের বাটনে চাপ দিন:""",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

            methods = db.get_payment_methods(active_only=True)
            keyboard = []
            for method in methods:
                m_key = method['key'].lower()
                emoji = "💗" if "bkash" in m_key else "🟠" if "nagad" in m_key else "🟣" if "rocket" in m_key else "💳"
                keyboard.append([
                    InlineKeyboardButton(
                        f"{emoji} {method['name']}",
                        callback_data=f"pay_{method['key']}"
                    )
                ])
            keyboard.append([InlineKeyboardButton(f"🎟 Coupon: {text.upper()} (Applied ✅)", callback_data="coupon_cart")])
            keyboard.append([InlineKeyboardButton("◀️ Back to Cart", callback_data="view_cart")])

            items = db.get_cart(user_id)
            items_list = "\n".join([f"•  <b>{html.escape(i['name'])}</b> — ৳{i['price']}" for i in items])
            discount_val = cart_total - new_total
            msg = f"""💳 <b>Select payment method:</b>

📦 <b>Cart Items ({len(items)} Courses):</b>
{items_list}

💰 <b>Price:</b> ৳{cart_total}
🎟 <b>Coupon Discount:</b> -৳{discount_val}
✅ <b>Total:</b> ৳{new_total}"""

            await update.message.reply_text(
                msg,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        clean_msg = message.lstrip("❌ ").strip()
        err_msg = f"❌ {clean_msg}"

        k_err = [
            [InlineKeyboardButton("🔄 Try Again", callback_data="coupon_cart")],
            [InlineKeyboardButton("🛒 Back to Cart", callback_data="view_cart")]
        ]
        await update.message.reply_text(
            err_msg,
            reply_markup=InlineKeyboardMarkup(k_err)
        )
        return

    course = db.get_course(course_id) if course_id else None
    if not course:
        cid = context.user_data.get("current_course") or context.user_data.get("checkout_course")
        course = db.get_course(cid) if cid else None
        if course:
            course_id = str(course.get("id", cid))

    course_price = course.get("price", 0) if course else 0
    course_cat = course.get("category", "") if course else ""
    course_name = course.get("name", "Course") if course else "Course"

    valid, discount, message, coupon_obj = db.validate_coupon_advanced(
        text, user_id, course_price, course_cat, course_id=course_id
    )

    if valid and course:
        new_price = max(0, course_price - discount)
        context.user_data["discounted_price"] = new_price
        context.user_data["applied_coupon"] = text.upper()
        context.user_data["checkout_type"] = "single"
        context.user_data["checkout_course"] = course_id
        context.user_data["checkout_course_name"] = course_name
        context.user_data["checkout_total"] = new_price

        if new_price == 0:
            db.add_purchase(user_id, course_id)
            db.use_coupon(text.upper(), user_id)
            access_link = course.get("access_link", "")
            if access_link:
                db.store_user_access_link(user_id, course_id, access_link)
            keyboard = []
            if access_link:
                dyn_link = await get_dynamic_access_link(context.bot, access_link, user_id)
                if dyn_link:
                    keyboard.append([InlineKeyboardButton("Go to Course", url=dyn_link)])
            keyboard.append([InlineKeyboardButton("🎓 My Courses", callback_data="my_courses_nav")])

            try:
                await update.message.delete()
            except Exception:
                pass
            await update.message.reply_text(
                f"""🎉 <b>কুপন দিয়ে ফ্রি কোর্স সফল হয়েছে!</b>

🏷️ <b>কুপন:</b> <code>{text.upper()}</code>
📘 <b>কোর্স:</b> {course['name']}
💰 <b>মূল্য:</b> ৳{course_price} → <b>ফ্রি!</b>

👇 ক্লাসে যুক্ত হতে নিচের বাটনে চাপ দিন:""",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        context.user_data["checkout_type"] = "single"
        context.user_data["checkout_course"] = course_id
        context.user_data["checkout_course_name"] = course["name"]
        context.user_data["checkout_total"] = new_price

        methods = db.get_payment_methods(active_only=True)
        keyboard = []
        for method in methods:
            m_key = method['key'].lower()
            emoji = "💗" if "bkash" in m_key else "🟠" if "nagad" in m_key else "🟣" if "rocket" in m_key else "💳"
            keyboard.append([
                InlineKeyboardButton(
                    f"{emoji} {method['name']}",
                    callback_data=f"pay_{method['key']}"
                )
            ])
        keyboard.append([InlineKeyboardButton(f"🎟 Coupon: {text.upper()} (Applied ✅)", callback_data=f"coupon_{course_id}")])
        keyboard.append([InlineKeyboardButton("◀️ Back", callback_data="cancel_buy")])

        discount_val = course_price - new_price
        msg = f"""💳 <b>Select payment method:</b>

📘 <b>Course:</b> {course['name']}
💰 <b>Price:</b> ৳{course_price}
🎟 <b>Coupon Discount:</b> -৳{discount_val}
✅ <b>Total:</b> ৳{new_price}"""

        course_image = course.get("image")
        try:
            await update.message.delete()
        except Exception:
            pass
        await update.message.reply_photo(
            photo=course_image,
            caption=msg,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        ) if course_image else await update.message.reply_text(
            msg,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    clean_msg = message.lstrip("❌ ").strip()
    err_msg = f"❌ {clean_msg}"

    k_err = [
        [InlineKeyboardButton("🔄 Try Again", callback_data=f"coupon_{course_id}")],
        [InlineKeyboardButton("◀️ Back to Course", callback_data=f"course_{course_id}")]
    ]
    await update.message.reply_text(
        err_msg,
        reply_markup=InlineKeyboardMarkup(k_err)
    )


async def handle_withdraw_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    step = context.user_data.get("withdraw_step")

    if text == "/cancel":
        context.user_data.pop("withdraw_step", None)
        context.user_data.pop("withdraw_amount", None)
        context.user_data.pop("withdraw_method", None)
        await update.message.reply_text("✕ উইথড্রয়াল প্রক্রিয়া বাতিল করা হয়েছে।", reply_markup=main_menu_keyboard(user_id))
        return

    if step == "amount":
        user_data = db.get_user(user_id) or {}
        balance = user_data.get("balance", 0)
        try:
            amount = int(text)
        except ValueError:
            await update.message.reply_text("⚠️ অনুগ্রহ করে সঠিক পূর্ণসংখ্যা লিখুন (যেমন: 100):")
            return

        if amount < MIN_WITHDRAW_AMOUNT:
            await update.message.reply_text(f"⚠️ সর্বনিম্ন উত্তোলনযোগ্য পরিমাণ `{MIN_WITHDRAW_AMOUNT}` ৳! পুনরায় লিখুন:")
            return

        if amount > balance:
            await update.message.reply_text(f"⚠️ আপনার অ্যাকাউন্টে পর্যাপ্ত ব্যালেন্স নেই! বর্তমান ব্যালেন্স: `{balance}` ৳। পুনরায় লিখুন:")
            return

        context.user_data["withdraw_amount"] = amount
        context.user_data["withdraw_step"] = "method_select"

        methods = db.get_payment_methods(active_only=True)
        keyboard = []
        row = []
        for m in methods:
            row.append(InlineKeyboardButton(f"💳 {m['name']}", callback_data=f"wdrpay_{m['key']}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        if db.is_home_button_enabled():
            keyboard.append([InlineKeyboardButton("✕ বাতিল", callback_data="back_to_main_menu")])

        await update.message.reply_text(
            f"💰 **উত্তোলনযোগ্য পরিমাণ:** `{amount}` ৳\n\n👇 **যে মাধ্যমে টাকা গ্রহণ করতে চান তা বেছে নিন:**",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif step == "account":
        account_number = text
        amount = context.user_data.get("withdraw_amount", 0)
        method = context.user_data.get("withdraw_method", "bKash")

        deducted = db.deduct_balance(user_id, amount)
        if not deducted:
            context.user_data.pop("withdraw_step", None)
            await update.message.reply_text("✕ দুঃখিত, আপনার অ্যাকাউন্টে পর্যাপ্ত ব্যালেন্স নেই!", reply_markup=main_menu_keyboard(user_id))
            return

        withdraw_id = f"WDR-{user_id}-{int(datetime.now().timestamp())}"
        w_data = {
            "user_id": user_id,
            "username": update.effective_user.username or "",
            "full_name": update.effective_user.full_name or "",
            "amount": amount,
            "method": method,
            "account": account_number,
            "status": "pending",
            "date": str(datetime.now())
        }
        db.add_withdrawal(withdraw_id, w_data)

        context.user_data.pop("withdraw_step", None)
        context.user_data.pop("withdraw_amount", None)
        context.user_data.pop("withdraw_method", None)

        await update.message.reply_text(
            f"""✅ **উইথড্র রিকোয়েস্ট সফলভাবে জমা হয়েছে!**
━━━━━━━━━━━━━━━━━━━━

🆔 **উইথড্র আইডি:** `{withdraw_id}`
💰 **টাকার পরিমাণ:** {amount} ৳
💳 **মাধ্যম:** {method} ({account_number})
📌 **স্ট্যাটাস:** 🟡 অপেক্ষমান (Pending)

এডমিন শীঘ্রই আপনার পেমেন্ট পাঠিয়ে রিকোয়েস্টটি অ্যাপ্রুভ করবেন।""",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(user_id)
        )

        admin_wdr_msg = f"""🔔 **New Withdrawal Request!**
━━━━━━━━━━━━━━━━━━━━

🆔 **Withdraw ID:** `{withdraw_id}`
👤 **User:** {update.effective_user.full_name} (@{update.effective_user.username or 'N/A'})
🆔 **User ID:** `{user_id}`
💰 **Amount:** {amount} ৳
💳 **Method:** {method}
📱 **Account:** `{account_number}`
📅 **Time:** {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"""

        admin_wdr_keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"apprwdr_{withdraw_id}"),
                InlineKeyboardButton("✕ Reject & Refund", callback_data=f"rejwdr_{withdraw_id}")
            ]
        ]

        for aid in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    aid, admin_wdr_msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(admin_wdr_keyboard)
                )
            except Exception as e:
                logger.error(f"অ্যাডমিন {aid} কে উইথড্র নোটিশ পাঠাতে ত্রুটি: {e}")


async def handle_trxid_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["awaiting_trxid"] = False
    user_id = update.effective_user.id

    if text == "/cancel" or text == "❌ Cancel" or text == "✕ Cancel":
        context.user_data.pop("pay_method", None)
        context.user_data.pop("pay_amount", None)
        await update.message.reply_text("✕ পেমেন্ট বাতিল করা হয়েছে।", reply_markup=main_menu_keyboard(user_id))
        return

    trxid = text.upper()
    method = context.user_data.get("pay_method", "Manual")
    total = context.user_data.get("pay_amount", 0)
    checkout_type = context.user_data.get("checkout_type", "single")
    applied_coupon = context.user_data.get("applied_coupon", "")

    order_id = db.generate_next_order_id()
    course_name = context.user_data.get("checkout_course_name", "Course")
    course_id = context.user_data.get("checkout_course", "")
    courses_in_order = context.user_data.get("checkout_courses", [course_id] if course_id else [])

    order_data = {
        "user_id": user_id,
        "username": update.effective_user.username or "",
        "full_name": update.effective_user.full_name or "",
        "course_id": course_id,
        "courses": courses_in_order,
        "course_name": course_name,
        "amount": total,
        "payment_method": method,
        "trxid": trxid,
        "coupon_code": applied_coupon,
        "status": "pending",
        "date": str(datetime.now()),
        "checkout_type": checkout_type
    }

    db.add_order(order_id, order_data)

    if applied_coupon:
        db.use_coupon(applied_coupon, user_id)

    if checkout_type == "cart":
        db.clear_cart(user_id)

    context.user_data.pop("discounted_price", None)
    context.user_data.pop("pay_method", None)
    context.user_data.pop("pay_amount", None)
    context.user_data.pop("checkout_course", None)
    context.user_data.pop("checkout_courses", None)
    context.user_data.pop("applied_coupon", None)

    course_label = "Courses" if checkout_type == "cart" else "Course"
    await update.message.reply_text(
        f"""✅ **Order Submitted Successfully!**
━━━━━━━━━━━━━━━━━━━━

📦 **Order ID:** `{format_order_id_display(order_id)}`
📘 **{course_label}:** {course_name}
💰 **Amount:** {total} ৳
💳 **Method:** {method}
🔑 **TrxID:** `{trxid}`

⏳ Admin will verify your payment soon. You'll receive course access once approved!""",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(user_id)
    )

    admin_msg = f"""🔔 **New Order Received!**
━━━━━━━━━━━━━━━━━━━━

📦 **Order ID:** `{format_order_id_display(order_id)}`
👤 **Student:** {update.effective_user.full_name} (@{update.effective_user.username or 'N/A'})
🆔 **User ID:** `{user_id}`
📖 **Course:** {course_name}
💰 **Amount:** {total} ৳
💳 **Method:** {method}
🔑 **TrxID:** `{trxid}`
🏷️ **Coupon:** `{applied_coupon or 'None'}`
📅 **Time:** {datetime.now().strftime('%Y-%m-%d %I:%M %p')}"""

    admin_keyboard = [
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{order_id}"),
            InlineKeyboardButton("✕ Reject", callback_data=f"reject_{order_id}")
        ]
    ]

    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(
                aid, admin_msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(admin_keyboard)
            )
        except Exception as e:
            logger.error(f"অ্যাডমিন {aid} কে মেসেজ পাঠাতে ত্রুটি: {e}")


# ==================== ADMIN PANEL (ENGLISH CLEAN BUTTONS) ====================

def get_admin_dashboard_keyboard(user_id: int) -> InlineKeyboardMarkup:
    keyboard = []

    # Row 1: Content
    r1 = []
    if db.has_permission(user_id, "course_manage"):
        r1.append(InlineKeyboardButton("📚 Courses", callback_data="adm_courses"))
    if db.has_permission(user_id, "ebook_manage"):
        r1.append(InlineKeyboardButton("📖 E-Books", callback_data="adm_ebooks"))
    if db.has_permission(user_id, "category_manage"):
        r1.append(InlineKeyboardButton("📁 Categories", callback_data="adm_categories"))
    if r1:
        keyboard.append(r1)

    # Row 2: Orders & Coupons
    r2 = []
    if db.has_permission(user_id, "orders"):
        r2.append(InlineKeyboardButton("⏳ Pending Orders", callback_data="adm_pending_orders"))
    if db.has_permission(user_id, "coupon"):
        r2.append(InlineKeyboardButton("🎟️ Coupons & Promo", callback_data="adm_coupons"))
    if r2:
        keyboard.append(r2)

    # Row 3: Payments & Withdrawals
    r3 = []
    if db.has_permission(user_id, "payment_settings"):
        r3.append(InlineKeyboardButton("💸 Withdrawals", callback_data="adm_withdrawals"))
        r3.append(InlineKeyboardButton("⚙️ Payment Gateway", callback_data="adm_payments"))
    if r3:
        keyboard.append(r3)

    # Row 4: Users & Broadcast
    r4 = []
    if db.has_permission(user_id, "user_manage") or db.has_permission(user_id, "admin_manage") or db.has_permission(user_id, "admin_permission"):
        r4.append(InlineKeyboardButton("👥 User Management", callback_data="adm_users"))
    if db.has_permission(user_id, "broadcast"):
        r4.append(InlineKeyboardButton("📢 Broadcast Notice", callback_data="adm_broadcast"))
    if r4:
        keyboard.append(r4)

    # Row 5: Analytics & Orders
    r5 = []
    if db.has_permission(user_id, "statistics"):
        r5.append(InlineKeyboardButton("📊 Analytics & Stats", callback_data="adm_stats"))
    if db.has_permission(user_id, "orders"):
        r5.append(InlineKeyboardButton("📦 All Orders", callback_data="adm_all_orders"))
    if r5:
        keyboard.append(r5)

    # Row 6: Settings & Keyboards
    if db.has_permission(user_id, "bot_settings"):
        keyboard.append([
            InlineKeyboardButton("⚙️ Bot Settings", callback_data="adm_bot_settings"),
            InlineKeyboardButton("⚙️ Keyboard Settings", callback_data="adm_keyboard_settings")
        ])
        keyboard.append([
            InlineKeyboardButton(f"🛠️ Maintenance: {'ON 🟢' if db.get_setting('maintenance_mode') == True else 'OFF 🔴'}", callback_data="adm_toggle_maintenance")
        ])

    return InlineKeyboardMarkup(keyboard)


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Access Denied! You are not an admin.")
        return

    stats = db.get_stats()
    maint_status = "🟢 ON" if db.get_setting("maintenance_mode") == True else "🔴 OFF"
    msg = f"""<blockquote>📊 <b>Live Analytics</b>
👥 Students: {stats['total_users']}
📚 Courses: {stats['total_courses']} | 📖 E-Books: {stats.get('total_ebooks', 0)}
📦 Orders: {stats['total_orders']} | ⏳ Pending: <code>{stats['pending_orders']}</code>
💸 Withdrawals: <code>{stats.get('pending_withdrawals', 0)}</code>
💵 Revenue: ৳{stats['total_revenue']} | Paid: ৳{stats.get('total_withdrawn', 0)}
🛠️ **Maintenance Mode:** {maint_status}</blockquote>

<blockquote>👇 <b>Select an option:</b></blockquote>"""

    keyboard = get_admin_dashboard_keyboard(user_id)
    if not keyboard.inline_keyboard:
        await update.message.reply_text("⛔ You currently do not have permissions for any admin modules. Please contact the Super Admin.")
        return

    await update.message.reply_text(
        msg, parse_mode="HTML", reply_markup=keyboard
    )


def get_admin_uview_keyboard(uid: int, is_user_adm: bool, is_root_owner: bool) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("✦ Grant / Revoke Course", callback_data=f"adm_ucourse_{uid}")],
        [InlineKeyboardButton("◈ Toggle Earnings Menu", callback_data=f"adm_utogearn_{uid}")],
        [InlineKeyboardButton("💰 Adjust Balance", callback_data=f"adm_ubal_{uid}")],
    ]
    if is_user_adm:
        keyboard.append([InlineKeyboardButton("🔐 Admin Permissions (পারমিশন পরিবর্তন)", callback_data=f"adm_perm_{uid}")])
        if not is_root_owner:
            keyboard.append([InlineKeyboardButton("❌ Demote from Admin", callback_data=f"adm_rmadmin_{uid}")])
    else:
        keyboard.append([InlineKeyboardButton("👑 Promote to Admin", callback_data=f"adm_makeadmin_{uid}")])

    keyboard.append([InlineKeyboardButton("➥ Send Message (DM)", callback_data=f"adm_udm_{uid}")])
    keyboard.append([InlineKeyboardButton("« Admin List", callback_data="adm_admin_list"), InlineKeyboardButton("« User List", callback_data="adm_userlist_1")])
    return InlineKeyboardMarkup(keyboard)


async def render_admin_permission_dashboard(query, context: ContextTypes.DEFAULT_TYPE, target_uid: int):
    u = db.get_user(target_uid)
    target_name = u.get("full_name") or u.get("username") or str(target_uid)
    perms = db.get_admin_permissions(target_uid)
    is_target_root = db.is_super_admin(target_uid)

    role_str = "👑 Super Admin (Full Access)" if is_target_root else "🛡️ Sub-Admin"

    msg = f"""<blockquote>🔐 <b>Permission Toggle:</b>
👤 <b>Admin:</b> {html.escape(target_name)} (<code>{target_uid}</code>)
👑 <b>Role:</b> {role_str}</blockquote>

<blockquote>💡 বাটনগুলোতে চাপ দিয়ে পারমিশন চালু (✅) বা বন্ধ (❌) করুন:</blockquote>"""

    keyboard = []
    for p_key, p_info in ADMIN_PERMISSION_DEFINITIONS.items():
        is_enabled = perms.get(p_key, True)
        icon = "✅" if is_enabled else "❌"
        btn_text = f"{icon} {p_info['emoji']} {p_info['name']}"
        keyboard.append([
            InlineKeyboardButton(btn_text, callback_data=f"adm_togperm_{target_uid}_{p_key}")
        ])

    bottom_row = []
    if not is_target_root:
        bottom_row.append(InlineKeyboardButton("🗑 Admin Remove", callback_data=f"adm_rmadmin_{target_uid}"))
    bottom_row.append(InlineKeyboardButton("◀️ Back", callback_data="adm_admin_list"))
    keyboard.append(bottom_row)

    if query.message.photo:
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def render_category_reorder_panel(query, context, parent_path: str = ""):
    clean_parent = str(parent_path).strip().replace(" / ", " > ").replace("/", " > ")
    folders = db.get_sub_folders(clean_parent, include_inactive=True)
    parent_title = f"Folder '{clean_parent}'" if clean_parent else "All Categories"
    msg = f"""🔀 <b>[ Reorder: {html.escape(parent_title)} ]</b>
━━━━━━━━━━━━━━━━━━━━
Use ⬆️ and ⬇️ buttons to change the order/position of items:

"""
    for i, f in enumerate(folders, 1):
        full_path = f"{clean_parent} > {f}" if clean_parent else f
        st_dot = "🟢" if db.is_category_active(full_path) else "🔴"
        msg += f"<b>{i}.</b> {st_dot} <code>{html.escape(f)}</code>\n"

    msg += "\n👇 <b>Tap ⬆️ or ⬇️ below to move:</b>"

    keyboard = []
    total = len(folders)
    for idx, f in enumerate(folders):
        row = [InlineKeyboardButton(f"📁 {f}", callback_data="noop_reorder")]
        if idx > 0:
            row.append(InlineKeyboardButton("⬆️", callback_data=f"adm_fld_reord_up_{idx}"))
        if idx < total - 1:
            row.append(InlineKeyboardButton("⬇️", callback_data=f"adm_fld_reord_dn_{idx}"))
        keyboard.append(row)

    back_cb = f"adm_dir_{clean_parent}" if clean_parent else "adm_dir_"
    keyboard.append([InlineKeyboardButton("« Done / Back", callback_data=back_cb)])

    if query.message.photo:
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def render_admin_folder_directory(query, context: ContextTypes.DEFAULT_TYPE, folder_path: str = ""):
    clean_path = str(folder_path).strip().replace(" / ", " > ").replace("/", " > ")
    context.user_data["active_dir"] = clean_path

    subfolders = db.get_sub_folders(clean_path, include_inactive=True)
    courses = db.get_courses_by_folder(clean_path, include_inactive=True) if clean_path else []

    segments = [s.strip() for s in clean_path.split(" > ") if s.strip()]
    current_name = segments[-1] if segments else "Root"
    parent_path = " > ".join(segments[:-1]) if len(segments) > 1 else ""

    keyboard = []

    # 1. Sub-folder buttons (2 per row)
    row = []
    for sf in subfolders:
        child_path = f"{clean_path} > {sf}" if clean_path else sf
        child_courses = db.get_courses_by_folder(child_path, include_inactive=True)
        child_sub_count = len(db.get_sub_folders(child_path, include_inactive=True))
        total_items = len(child_courses) if len(child_courses) > 0 else child_sub_count
        badge = f" ({total_items})" if total_items > 0 else ""
        is_sf_active = db.is_category_active(child_path)
        status_dot = "" if is_sf_active else "🔴 [OFF] "
        row.append(InlineKeyboardButton(f"{status_dot}{sf}{badge}", callback_data=f"adm_dir_{child_path}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # 2. Courses in this folder (if any)
    for c in courses[:25]:
        price_tag = f" ({c['price']}৳)" if c.get('price', 0) > 0 else " (Free)"
        status_dot = "🔴 " if c.get("status") == "inactive" else ""
        keyboard.append([
            InlineKeyboardButton(f"{status_dot}{c['name']} — {c['price']}৳" if c.get('price', 0) > 0 else f"{status_dot}{c['name']} — Free 🎁", callback_data=f"adm_edit_{c['id']}")
        ])

    # 3. Contextual Actions (Add Course & Add Sub-Folder)
    if not clean_path:
        keyboard.append([
            InlineKeyboardButton("➕ Add Category", callback_data="adm_fld_addfolder"),
            InlineKeyboardButton("➕ Add New Course", callback_data="adm_add_course")
        ])
        if len(subfolders) > 1:
            keyboard.append([
                InlineKeyboardButton("🔀 Reorder Categories", callback_data="adm_fld_reorder_")
            ])
        keyboard.append([InlineKeyboardButton("« Admin Menu", callback_data="adm_main")])

        sub_list_str = "\n".join([f"  📁 {'🔴 [OFF] ' if not db.is_category_active(s) else ''}<b>{html.escape(s)}</b> <i>({len(db.get_courses_by_folder(s, include_inactive=True)) or len(db.get_sub_folders(s, include_inactive=True))} items)</i>" for s in subfolders]) if subfolders else "  <i>(কোনো ক্যাটাগরি নেই)</i>"
        msg = f"""<blockquote>📂 <b>[ Category & Directory Manager ]</b></blockquote>

<blockquote>📂 <b>Categories ({len(subfolders)}):</b>
{sub_list_str}</blockquote>

<blockquote>💡 <b>প্রতিটি ক্যাটাগরিতে ঢুকে সাব-ফোল্ডার, অন/অফ (Status Toggle) বা সরাসরি কোর্স যুক্ত করতে পারেন।</b></blockquote>"""
    else:
        keyboard.append([
            InlineKeyboardButton("➕ Add Course", callback_data="adm_fld_addcourse"),
            InlineKeyboardButton("➕ Add Sub-Folder", callback_data="adm_fld_addfolder")
        ])

        siblings = db.get_sub_folders(parent_path, include_inactive=True)
        cur_pos_str = ""
        if current_name in siblings:
            cur_pos_str = f"\n🔢 <b>Position:</b> #{siblings.index(current_name) + 1} of {len(siblings)}"

        # Clean Move buttons with clear labels
        move_row = [
            InlineKeyboardButton("⬆️ Move Up", callback_data=f"adm_fld_moveup_{current_name}"),
            InlineKeyboardButton("⬇️ Move Down", callback_data=f"adm_fld_movedown_{current_name}")
        ]
        keyboard.append(move_row)

        nest_row = []
        if len(segments) > 1:
            nest_row.append(InlineKeyboardButton("⬅️ Move Out (Parent)", callback_data=f"adm_fld_mleft_{current_name}"))
        if len(siblings) > 1:
            nest_row.append(InlineKeyboardButton("➡️ Move In (Sub-Folder)", callback_data=f"adm_fld_mright_{current_name}"))
        if nest_row:
            keyboard.append(nest_row)

        is_active = db.is_category_active(clean_path)
        status_toggle_btn = InlineKeyboardButton(f"🔄 Status: {'🟢 Active (ON)' if is_active else '🔴 Inactive (OFF)'}", callback_data=f"adm_fld_togglestatus_{current_name}")
        del_btn = InlineKeyboardButton(f"✕ Delete '{current_name}'", callback_data="adm_fld_delfolder")
        rename_btn = InlineKeyboardButton("✏️ Rename", callback_data=f"adm_fld_rename_{current_name}")
        
        keyboard.append([status_toggle_btn])
        keyboard.append([rename_btn, del_btn])

        back_title = f"« Back to {segments[-2]}" if len(segments) > 1 else "« All Categories"
        back_btn = InlineKeyboardButton(back_title, callback_data=f"adm_dir_{parent_path}")
        keyboard.append([back_btn])

        if len(segments) > 1:
            keyboard.append([InlineKeyboardButton("📂 All Categories (Root)", callback_data="adm_dir_")])

        path_display = html.escape(" ➔ ".join(segments))
        status_display = "🟢 <b>Active (ON)</b>" if is_active else "🔴 <b>Inactive (OFF - Hidden from students)</b>"
        sub_list_str = "\n".join([f"  📁 {'🔴 [OFF] ' if not db.is_category_active(clean_path + ' > ' + s) else ''}<b>{html.escape(s)}</b> <i>({len(db.get_courses_by_folder(clean_path + ' > ' + s, include_inactive=True)) or len(db.get_sub_folders(clean_path + ' > ' + s, include_inactive=True))} items)</i>" for s in subfolders]) if subfolders else "  <i>(কোনো সাব-ফোল্ডার নেই)</i>"
        course_list_str = "\n".join([f"  • {'[DISABLED] ' if c.get('status') == 'inactive' else ''}<b>{html.escape(c['name'])}</b> ➔ <code>৳{c.get('price', 0)}</code>" for c in courses]) if courses else "  <i>(এই ফোল্ডারে এখনো কোনো কোর্স নেই)</i>"

        msg = f"""<blockquote>📁 <b>[ Directory: <code>{path_display}</code> ]</b>
📌 <b>Visibility / Status:</b> {status_display}{cur_pos_str}</blockquote>

<blockquote>📂 <b>Sub-Folders ({len(subfolders)}):</b>
{sub_list_str}</blockquote>

<blockquote>📚 <b>Courses ({len(courses)}):</b>
{course_list_str}</blockquote>

<blockquote>💡 <b>নিচের বাটন চেপে ক্যাটাগরি অন/অফ, নতুন কোর্স বা সাব-ফোল্ডার পরিচালনা করুন:</b></blockquote>"""

    if query.message.photo:
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def render_admin_ebook_folder_directory(query, context: ContextTypes.DEFAULT_TYPE, folder_path: str = ""):
    clean_path = str(folder_path).strip().replace(" / ", " > ").replace("/", " > ")
    context.user_data["active_eb_dir"] = clean_path

    subfolders = db.get_ebook_sub_folders(clean_path, include_inactive=True)
    ebooks = db.get_ebooks_by_folder(clean_path, include_inactive=True) if clean_path else []

    segments = [s.strip() for s in clean_path.split(" > ") if s.strip()]
    current_name = segments[-1] if segments else "Root"
    parent_path = " > ".join(segments[:-1]) if len(segments) > 1 else ""

    keyboard = []

    # 1. Sub-folder buttons (2 per row)
    row = []
    for sf in subfolders:
        child_path = f"{clean_path} > {sf}" if clean_path else sf
        child_ebooks = db.get_ebooks_by_folder(child_path, include_inactive=True)
        child_sub_count = len(db.get_ebook_sub_folders(child_path, include_inactive=True))
        total_items = len(child_ebooks) if len(child_ebooks) > 0 else child_sub_count
        badge = f" ({total_items})" if total_items > 0 else ""
        is_sf_active = db.is_ebook_category_active(child_path)
        status_dot = "" if is_sf_active else "🔴 [OFF] "
        row.append(InlineKeyboardButton(f"{status_dot}{sf}{badge}", callback_data=f"adm_ebdir_{child_path}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # 2. E-Books in this folder (if any)
    for eb in ebooks[:25]:
        price_tag = f" ({eb['price']}৳)" if eb.get('price', 0) > 0 else " (Free)"
        status_dot = "🔴 " if eb.get("status") == "inactive" else ""
        keyboard.append([
            InlineKeyboardButton(f"{status_dot}{eb['name']} — {eb['price']}৳" if eb.get('price', 0) > 0 else f"{status_dot}{eb['name']} — Free 🎁", callback_data=f"adm_vieweb_{eb['id']}")
        ])

    # 3. Contextual Actions (Add E-Book & Add Sub-Folder)
    if not clean_path:
        keyboard.append([
            InlineKeyboardButton("➕ Add Category", callback_data="adm_ebfld_addfolder"),
            InlineKeyboardButton("➕ Add New E-Book", callback_data="adm_ebfld_addebook")
        ])
        if len(subfolders) > 1:
            keyboard.append([
                InlineKeyboardButton("⬆️ Move Up", callback_data="adm_ebfld_moveup"),
                InlineKeyboardButton("⬇️ Move Down", callback_data="adm_ebfld_movedown")
            ])
        keyboard.append([InlineKeyboardButton("« Admin Menu", callback_data="adm_main")])

        sub_list_str = "\n".join([f"  📁 {'🔴 [OFF] ' if not db.is_ebook_category_active(s) else ''}<b>{html.escape(s)}</b> <i>({len(db.get_ebooks_by_folder(s, include_inactive=True)) or len(db.get_ebook_sub_folders(s, include_inactive=True))} items)</i>" for s in subfolders]) if subfolders else "  <i>(কোনো ক্যাটাগরি নেই)</i>"
        msg = f"""<blockquote>📖 <b>[ E-Book Category & Directory Manager ]</b></blockquote>

<blockquote>📂 <b>Categories ({len(subfolders)}):</b>
{sub_list_str}</blockquote>

<blockquote>💡 <b>প্রতিটি ক্যাটাগরিতে ঢুকে সাব-ফোল্ডার, অন/অফ (Status Toggle) বা সরাসরি ই-বুক / PDF যুক্ত করতে পারেন।</b></blockquote>"""
    else:
        keyboard.append([
            InlineKeyboardButton("➕ Add E-Book", callback_data="adm_ebfld_addebook"),
            InlineKeyboardButton("➕ Add Sub-Folder", callback_data="adm_ebfld_addfolder")
        ])

        move_row = [
            InlineKeyboardButton("⬅️", callback_data=f"adm_ebfld_mleft_{current_name}"),
            InlineKeyboardButton("⬆️", callback_data=f"adm_ebfld_moveup_{current_name}"),
            InlineKeyboardButton("⬇️", callback_data=f"adm_ebfld_movedown_{current_name}"),
            InlineKeyboardButton("➡️", callback_data=f"adm_ebfld_mright_{current_name}")
        ]
        keyboard.append(move_row)

        is_active = db.is_ebook_category_active(clean_path)
        status_toggle_btn = InlineKeyboardButton(f"🔄 Status: {'🟢 Active (ON)' if is_active else '🔴 Inactive (OFF)'}", callback_data=f"adm_ebfld_togglestatus_{current_name}")
        del_btn = InlineKeyboardButton(f"✕ Delete '{current_name}'", callback_data="adm_ebfld_delfolder")
        rename_btn = InlineKeyboardButton("✏️ Rename", callback_data=f"adm_ebfld_rename_{current_name}")
        
        keyboard.append([status_toggle_btn])
        keyboard.append([rename_btn, del_btn])

        back_title = f"« Back to {segments[-2]}" if len(segments) > 1 else "« All Categories"
        back_btn = InlineKeyboardButton(back_title, callback_data=f"adm_ebdir_{parent_path}")
        keyboard.append([back_btn])

        if len(segments) > 1:
            keyboard.append([InlineKeyboardButton("📂 All Categories (Root)", callback_data="adm_ebdir_")])

        path_display = html.escape(" ➔ ".join(segments))
        status_display = "🟢 <b>Active (ON)</b>" if is_active else "🔴 <b>Inactive (OFF - Hidden from students)</b>"
        sub_list_str = "\n".join([f"  📁 {'🔴 [OFF] ' if not db.is_ebook_category_active(clean_path + ' > ' + s) else ''}<b>{html.escape(s)}</b> <i>({len(db.get_ebooks_by_folder(clean_path + ' > ' + s, include_inactive=True)) or len(db.get_ebook_sub_folders(clean_path + ' > ' + s))} items)</i>" for s in subfolders]) if subfolders else "  <i>(কোনো সাব-ফোল্ডার নেই)</i>"
        ebook_list_str = "\n".join([f"  • {'[DISABLED] ' if eb.get('status') == 'inactive' else ''}<b>{html.escape(eb['name'])}</b> ➔ <code>৳{eb.get('price', 0)}</code>" for eb in ebooks]) if ebooks else "  <i>(এই ফোল্ডারে এখনো কোনো ই-বুক নেই)</i>"

        msg = f"""<blockquote>📁 <b>[ E-Book Directory: <code>{path_display}</code> ]</b>
📌 <b>Visibility / Status:</b> {status_display}</blockquote>

<blockquote>📂 <b>Sub-Folders ({len(subfolders)}):</b>
{sub_list_str}</blockquote>

<blockquote>📖 <b>E-Books ({len(ebooks)}):</b>
{ebook_list_str}</blockquote>
<blockquote>💡 <b>নিচের বাটন চেপে ক্যাটাগরি অন/অফ, নতুন ই-বুক বা সাব-ফোল্ডার পরিচালনা করুন:</b></blockquote>"""

    if query.message.photo:
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


def get_action_select_keyboard(is_edit=False, r_idx=0, c_idx=0):
    prefix = "adm_kb_edact_" if is_edit else "adm_kb_act_"
    keyboard = [
        [InlineKeyboardButton("⚙️ Built-in Actions", callback_data=f"{prefix}grp_builtin")],
        [InlineKeyboardButton("📂 Course Categories", callback_data=f"{prefix}grp_cats")],
        [InlineKeyboardButton("🏷️ Course Sub-categories", callback_data=f"{prefix}grp_subcats")],
        [InlineKeyboardButton("✍️ Custom Callback Data", callback_data=f"{prefix}custom")],
    ]
    if is_edit:
        keyboard.append([InlineKeyboardButton("« Back", callback_data=f"adm_kb_edit_btn_{r_idx}_{c_idx}")])
    else:
        keyboard.append([InlineKeyboardButton("« Cancel", callback_data="adm_keyboard_settings")])
    return InlineKeyboardMarkup(keyboard)


def get_builtin_actions_keyboard(is_edit=False, r_idx=0, c_idx=0):
    prefix = "adm_kb_edact_" if is_edit else "adm_kb_act_"
    keyboard = [
        [InlineKeyboardButton("🛍️ Cart", callback_data=f"{prefix}cart"), InlineKeyboardButton("👤 Profile", callback_data=f"{prefix}profile")],
        [InlineKeyboardButton("ℹ Info / Help", callback_data=f"{prefix}info"), InlineKeyboardButton("⚙ Admin Panel", callback_data=f"{prefix}admin")],
        [InlineKeyboardButton("📚 Browse Courses", callback_data=f"{prefix}browse_categories"), InlineKeyboardButton("🎓 My Courses", callback_data=f"{prefix}my_courses_nav")],
        [InlineKeyboardButton("📖 My E-Books", callback_data=f"{prefix}my_ebooks_nav")],
        [InlineKeyboardButton("« Back", callback_data=f"{prefix}menu")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_categories_actions_keyboard(is_edit=False, r_idx=0, c_idx=0):
    prefix = "adm_kb_edact_" if is_edit else "adm_kb_act_"
    keyboard = []
    
    for cat in db.categories.keys():
        keyboard.append([InlineKeyboardButton(f"{cat}", callback_data=f"{prefix}cat_{cat}")])
        
    keyboard.append([InlineKeyboardButton("« Back", callback_data=f"{prefix}menu")])
    return InlineKeyboardMarkup(keyboard)


def get_subcat_category_select_keyboard(is_edit=False, r_idx=0, c_idx=0):
    prefix = "adm_kb_edact_" if is_edit else "adm_kb_act_"
    keyboard = []
    
    for cat in db.categories.keys():
        keyboard.append([InlineKeyboardButton(f"📂 {cat}", callback_data=f"{prefix}subcatlist_{cat}")])
        
    keyboard.append([InlineKeyboardButton("« Back", callback_data=f"{prefix}menu")])
    return InlineKeyboardMarkup(keyboard)


def get_subcategories_actions_keyboard(category: str, is_edit=False, r_idx=0, c_idx=0):
    prefix = "adm_kb_edact_" if is_edit else "adm_kb_act_"
    keyboard = []
    
    subcats = db.categories.get(category, [])
    for sub in subcats:
        keyboard.append([InlineKeyboardButton(f"🏷️ {sub}", callback_data=f"{prefix}subcat_{category}_{sub}")])
        
    keyboard.append([InlineKeyboardButton("« Back", callback_data=f"{prefix}grp_subcats")])
    return InlineKeyboardMarkup(keyboard)


async def render_keyboard_settings_panel(query):
    custom_kb = db.get_custom_keyboards()
    home_btn_enabled = db.is_home_button_enabled()
    home_btn_st = "🟢 Enabled (Active)" if home_btn_enabled else "🔴 Disabled (Hidden)"
    view_all_enabled = db.is_view_all_courses_enabled()
    view_all_st = "🟢 Enabled (Active)" if view_all_enabled else "🔴 Disabled (Hidden)"
    msg = f"""⚙️ <b>Keyboard Layout Settings</b>
━━━━━━━━━━━━━━━━━━━━
Here is the current ReplyKeyboardMarkup layout. (Buttons with 👑 are Admin-only)

• <b>Inline HOME Button:</b> {home_btn_st}
• <b>Browse All Courses Button:</b> {view_all_st}

"""
    buttons_grid = custom_kb.get("buttons", [])
    for r_idx, row in enumerate(buttons_grid):
        row_str = []
        for c_idx, btn in enumerate(row):
            adm_marker = " 👑" if btn.get("admin_only") else ""
            row_str.append(f"<code>[{html.escape(btn['text'])}{adm_marker} -> {html.escape(btn.get('action', ''))}]</code>")
        msg += f"<b>Row {r_idx + 1}:</b> " + ", ".join(row_str) + "\n"
    
    if not buttons_grid:
        msg += "<i>No custom buttons configured.</i>\n"
        
    msg += "\n👇 Select an operation below:"
    
    keyboard = [
        [
            InlineKeyboardButton(f"⚜️ HOME: {'ON 🟢' if home_btn_enabled else 'OFF 🔴'}", callback_data="adm_toggle_home_btn_kb"),
            InlineKeyboardButton(f"📚 All Courses: {'ON 🟢' if view_all_enabled else 'OFF 🔴'}", callback_data="adm_toggle_view_all_courses_kb")
        ],
        [
            InlineKeyboardButton("➕ Add Button", callback_data="adm_kb_add"),
            InlineKeyboardButton("🗑️ Delete Button", callback_data="adm_kb_del_list")
        ],
        [
            InlineKeyboardButton("✏️ Edit Button", callback_data="adm_kb_edit_list"),
            InlineKeyboardButton("📐 Resize Columns", callback_data="adm_kb_resize_cols_prompt")
        ],
        [
            InlineKeyboardButton("« Admin Menu", callback_data="adm_main")
        ]
    ]
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== ADMIN REFERRALS MANAGEMENT HUB ====================

async def render_admin_referrals_hub(query, context):
    stats = db.get_referral_global_stats()
    is_enabled = stats.get("is_enabled", True)
    bonus_amt = stats.get("bonus_amount", 0)
    st_text = "🟢 Enabled (Active)" if is_enabled else "🔴 Disabled (Paused)"
    st_btn_text = "🔴 Pause Referral System" if is_enabled else "🟢 Enable Referral System"

    msg = f"""<blockquote>🎁 <b>[ Refer & Earn Management Hub ]</b></blockquote>

<blockquote>⚙️ <b>System Settings:</b>
• <b>System Status:</b> <b>{st_text}</b>
• <b>Bonus Per Referral:</b> <code>৳{bonus_amt} BDT</code>
• <b>Conversion Trigger:</b> <i>পেইড কোর্স অর্ডার অনুমোদন (Paid Purchase Approval)</i></blockquote>

<blockquote>📊 <b>Global Referral Statistics:</b>
• <b>Total Referrers:</b> <code>{stats.get('total_referrers', 0)}</code> users
• <b>Total Joined via Links:</b> <code>{stats.get('total_joined', 0)}</code> students
• <b>Successful Paid Referrals:</b> <code>{stats.get('total_converted', 0)}</code> conversions
• <b>Total Referral Bonus Paid:</b> <code>৳{stats.get('total_paid_out', stats.get('total_withdrawn', 0))} BDT</code>
• <b>Total User Balance Held:</b> <code>৳{stats.get('total_balance', 0)} BDT</code></blockquote>

<blockquote>👇 <b>Admin Control Options:</b></blockquote>"""

    keyboard = [
        [InlineKeyboardButton(st_btn_text, callback_data="adm_tog_referral")],
        [InlineKeyboardButton("💵 Set Bonus Amount", callback_data="adm_ref_set_bonus")],
        [InlineKeyboardButton("📋 Referrers & Balances List", callback_data="adm_reflist_1")],
        [InlineKeyboardButton("🔍 Search Referrals", callback_data="adm_ref_search")],
        [InlineKeyboardButton("« Coupons & Rewards Hub", callback_data="adm_coupons")]
    ]
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def render_admin_referrers_list(query, context, page: int = 1):
    per_page = 8
    referrers, total_pages, total_count = db.get_paginated_referrers(page=page, per_page=per_page)

    msg = f"""<blockquote>👥 <b>[ Referrers & Balances Leaderboard ]</b></blockquote>
<blockquote>📌 <b>Total Referrers:</b> <code>{total_count}</code> | <b>Page:</b> <code>{page}/{max(1, total_pages)}</code></blockquote>

"""
    if not referrers:
        msg += "<i>বর্তমানে কোনো রেফারেলের রেকর্ড নেই।</i>\n"

    keyboard = []
    for u in referrers:
        uid = u["user_id"]
        uname = u.get("full_name") or u.get("username") or f"User {uid}"
        ref_c = u.get("referral_count", 0)
        bal = u.get("balance", 0)
        earn_st = "🟢" if u.get("earnings_enabled") else "⚪"
        keyboard.append([
            InlineKeyboardButton(f"{earn_st} {uname[:15]} — 👥 {ref_c} | 💰 ৳{bal}", callback_data=f"adm_refuview_{uid}")
        ])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"adm_reflist_{page - 1}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"adm_reflist_{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([
        InlineKeyboardButton("🔍 Search User", callback_data="adm_ref_search"),
        InlineKeyboardButton("« Referrals Hub", callback_data="adm_referrals")
    ])

    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def render_admin_referral_user_view(query, context, user_id: int):
    u = db.get_user(user_id)
    if not u:
        await query.answer("User not found!", show_alert=True)
        return

    full_name = u.get("full_name") or "N/A"
    username = f"@{u['username']}" if u.get("username") else "N/A"
    ref_count = u.get("referral_count", 0)
    bal = u.get("balance", 0)
    joined_date = u.get("joined_date", "")[:16]
    referred_by = u.get("referred_by")
    ref_by_text = f"<code>{referred_by}</code>" if referred_by else "<i>Direct / None</i>"
    is_earn = "🟢 Enabled" if u.get("earnings_enabled") else "🔴 Disabled"

    referred_list = u.get("referred_users", [])
    total_joined = len(referred_list)
    total_converted = sum(1 for item in referred_list if isinstance(item, dict) and item.get("converted"))

    msg = f"""<blockquote>👤 <b>[ User Referral Profile ]</b></blockquote>

<blockquote>🆔 <b>User ID:</b> <code>{user_id}</code>
👤 <b>Name:</b> <b>{html.escape(full_name)}</b>
🔗 <b>Username:</b> {username}
📅 <b>Joined:</b> <code>{joined_date}</code>
🤝 <b>Referred By:</b> {ref_by_text}</blockquote>

<blockquote>💰 <b>Referral & Wallet Stats:</b>
• <b>Successful Paid Referrals:</b> <code>{ref_count}</code>
• <b>Total Joined via Link:</b> <code>{total_joined}</code>
• <b>Paid Conversions:</b> <code>{total_converted}</code>
• <b>Current Wallet Balance:</b> <code>৳{bal} BDT</code>
• <b>Cash Withdraw Permission:</b> <b>{is_earn}</b></blockquote>

<blockquote>👥 <b>Referred Students List:</b></blockquote>"""

    if not referred_list:
        msg += "\n<i>এই ইউজারের লিংকে এখনও কেউ জয়েন করেনি।</i>"
    else:
        for idx, ref_item in enumerate(referred_list[-8:], 1):
            if isinstance(ref_item, dict):
                r_uid = ref_item.get("user_id")
                r_name = ref_item.get("full_name") or ref_item.get("username") or str(r_uid)
                r_conv = "🟢 Paid" if ref_item.get("converted") else "🟡 Joined"
                msg += f"\n{idx}. <b>{html.escape(r_name)}</b> (<code>{r_uid}</code>) — {r_conv}"
            else:
                msg += f"\n{idx}. <code>{ref_item}</code>"

    keyboard = [
        [InlineKeyboardButton("👤 Open Full User Profile", callback_data=f"adm_uview_{user_id}")],
        [InlineKeyboardButton("📋 Back to Referrers List", callback_data="adm_reflist_1"), InlineKeyboardButton("« Referrals Hub", callback_data="adm_referrals")]
    ]
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def prompt_add_kb_row(query, context):
    custom_kb = db.get_custom_keyboards()
    keyboard = []
    
    keyboard.append([InlineKeyboardButton("➕ New Row at Top", callback_data="adm_kb_row_new_top")])
    
    buttons_grid = custom_kb.get("buttons", [])
    for r_idx in range(len(buttons_grid)):
        keyboard.append([InlineKeyboardButton(f"Row {r_idx + 1}", callback_data=f"adm_kb_row_{r_idx}")])
        
    keyboard.append([InlineKeyboardButton("➕ New Row at Bottom", callback_data="adm_kb_row_new_bottom")])
    keyboard.append([InlineKeyboardButton("« Cancel", callback_data="adm_keyboard_settings")])
    
    await query.edit_message_text(
        "👇 <b>Choose which row to place the new button on:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def render_button_edit_panel(query, context, r_idx, c_idx):
    custom_kb = db.get_custom_keyboards()
    btn = custom_kb["buttons"][r_idx][c_idx]
    
    adm_marker = "🟢 Yes (Admins Only)" if btn.get("admin_only") else "🔴 No (All Users)"
    msg = f"""✏️ <b>Modify Keyboard Button</b>
━━━━━━━━━━━━━━━━━━━━
• <b>Text:</b> <code>{html.escape(btn['text'])}</code>
• <b>Action:</b> <code>{html.escape(btn.get('action', ''))}</code>
• <b>Admin Only:</b> {adm_marker}
• <b>Position:</b> Row {r_idx+1}, Column {c_idx+1}

Choose what you want to edit:"""

    keyboard = [
        [InlineKeyboardButton("✏️ Edit Text", callback_data="adm_kb_edt_text"), InlineKeyboardButton("🔗 Edit Action", callback_data="adm_kb_edt_action")],
        [InlineKeyboardButton("↔️ Move Position", callback_data="adm_kb_edt_move"), InlineKeyboardButton("👑 Toggle Admin-Only", callback_data="adm_kb_edt_adm")],
        [InlineKeyboardButton("« Keyboard Settings", callback_data="adm_keyboard_settings")]
    ]
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def render_home_buttons_panel(query):
    grid = get_home_keyboard_grid()
    msg = """🏡 <b>Home Screen Inline Buttons Settings</b>
━━━━━━━━━━━━━━━━━━━━
Here is the current home inline buttons layout. (🟢 = Enabled / 🔴 = Disabled)

"""
    for r_idx, row in enumerate(grid):
        row_str = []
        for c_idx, btn in enumerate(row):
            status = "🟢" if btn.get("enabled", True) else "🔴"
            row_str.append(f"{status} <code>[{html.escape(btn['text'])} -> {html.escape(btn.get('action', ''))}]</code>")
        msg += f"<b>Row {r_idx + 1}:</b> " + " | ".join(row_str) + "\n"
        
    if not grid:
        msg += "<i>No buttons configured.</i>\n"
        
    msg += "\n👇 Select an operation below:"
    
    keyboard = [
        [
            InlineKeyboardButton("➕ Add Button", callback_data="adm_hbtn_add"),
            InlineKeyboardButton("🗑️ Delete Button", callback_data="adm_hbtn_del_list")
        ],
        [
            InlineKeyboardButton("✏️ Modify Button", callback_data="adm_hbtn_edit_list"),
            InlineKeyboardButton("📐 Resize Columns", callback_data="adm_hbtn_resize_prompt")
        ],
        [
            InlineKeyboardButton("« Bot Settings", callback_data="adm_bot_settings")
        ]
    ]
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def render_info_buttons_panel(query):
    cfg = db.get_info_settings()
    items = cfg.get("items", {})
    custom_btns = cfg.get("custom_buttons", [])
    header_txt = cfg.get("header_text", "")
    if len(header_txt) > 40:
        header_preview = header_txt[:40].replace("\n", " ") + "..."
    else:
        header_preview = header_txt.replace("\n", " ")

    def status_icon(key):
        return "🟢" if items.get(key, {}).get("enabled", True) else "🔴"

    def content_status(key):
        return "✏️ Custom" if items.get(key, {}).get("content", "").strip() else "📄 Default"

    custom_str = ""
    if custom_btns:
        custom_str = "\n<b>Custom Buttons:</b>\n" + "\n".join(
            [f"• {html.escape(b['label'])} [{'🟢 ON' if b.get('enabled', True) else '🔴 OFF'}]" for b in custom_btns]
        ) + "\n"

    msg = f"""ℹ️ <b>[ Info Button & Description Settings ]</b>
━━━━━━━━━━━━━━━━━━━━
Manage the buttons, visibility, and text descriptions of the <b>ℹ Info</b> menu:

<b>Menu Sections & Current Status:</b>
• 💬 Contact Support: {status_icon('contact')} [{content_status('contact')}]
• 📖 How to Buy: {status_icon('how_to_buy')} [{content_status('how_to_buy')}]
• 🎓 About StudyMart: {status_icon('about')} [{content_status('about')}]
• 📜 Terms & Policy: {status_icon('terms')} [{content_status('terms')}]
{custom_str}
<b>Header Text Preview:</b>
↳ <code>{html.escape(header_preview)}</code>

👇 <b>Click any section below to edit its text description or toggle ON/OFF:</b>"""

    keyboard = [
        [
            InlineKeyboardButton(f"{status_icon('contact')} 💬 Contact Support", callback_data="adm_infoitem_contact"),
            InlineKeyboardButton(f"{status_icon('how_to_buy')} 📖 How to Buy", callback_data="adm_infoitem_how_to_buy")
        ],
        [
            InlineKeyboardButton(f"{status_icon('about')} 🎓 About StudyMart", callback_data="adm_infoitem_about"),
            InlineKeyboardButton(f"{status_icon('terms')} 📜 Terms & Policy", callback_data="adm_infoitem_terms")
        ],
        [
            InlineKeyboardButton("➕ Add Custom Button", callback_data="adm_infobtn_add"),
            InlineKeyboardButton("✏️ Edit Header Text", callback_data="adm_editmsg_info_message")
        ]
    ]
    if custom_btns:
        keyboard.append([InlineKeyboardButton("🗑️ Delete Custom Button", callback_data="adm_infobtn_del_list")])
    keyboard.append([
        InlineKeyboardButton("« Bot Settings", callback_data="adm_bot_settings"),
        InlineKeyboardButton("« Admin Menu", callback_data="adm_main")
    ])

    if query.message.photo:
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def render_info_item_editor(query, item_key: str):
    cfg = db.get_info_settings()
    item = cfg.get("items", {}).get(item_key, {})
    label = item.get("label") or item_key
    enabled = item.get("enabled", True)
    custom_content = item.get("content", "").strip()
    status_str = "🟢 Enabled (ON)" if enabled else "🔴 Disabled (OFF)"
    
    if custom_content:
        content_preview = custom_content
        mode_str = "🟢 <b>Custom Text</b>"
    else:
        content_preview = get_default_info_content(item_key)
        mode_str = "⚪ <b>Default Template</b>"

    if len(content_preview) > 500:
        preview_disp = content_preview[:500] + "\n...(preview truncated)..."
    else:
        preview_disp = content_preview

    msg = f"""ℹ️ <b>[ Info Section: {html.escape(label)} ]</b>
━━━━━━━━━━━━━━━━━━━━
📌 <b>Visibility:</b> {status_str}
📝 <b>Content Status:</b> {mode_str}

📖 <b>Current Description / Text Preview:</b>
<blockquote>{html.escape(preview_disp)}</blockquote>

👇 <b>Choose an action:</b>"""

    keyboard = [
        [
            InlineKeyboardButton(f"🔄 Toggle: {'ON 🟢' if enabled else 'OFF 🔴'}", callback_data=f"adm_infotog_{item_key}"),
            InlineKeyboardButton("✏️ Edit Description", callback_data=f"adm_infoedit_{item_key}")
        ]
    ]
    if custom_content:
        keyboard.append([InlineKeyboardButton("🔄 Reset to Default Template", callback_data=f"adm_inforeset_{item_key}")])
    keyboard.append([InlineKeyboardButton("« Back to Info Settings", callback_data="adm_info_buttons")])

    if query.message.photo:
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def prompt_add_hbtn_row(query, context):
    grid = get_home_keyboard_grid()
    keyboard = []
    
    keyboard.append([InlineKeyboardButton("➕ New Row at Top", callback_data="adm_hbtn_row_new_top")])
    
    for r_idx in range(len(grid)):
        keyboard.append([InlineKeyboardButton(f"Row {r_idx + 1}", callback_data=f"adm_hbtn_row_{r_idx}")])
        
    keyboard.append([InlineKeyboardButton("➕ New Row at Bottom", callback_data="adm_hbtn_row_new_bottom")])
    keyboard.append([InlineKeyboardButton("« Cancel", callback_data="adm_home_buttons")])
    
    await query.edit_message_text(
        "👇 <b>Choose which row to place the new button on:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def render_hbtn_edit_panel(query, context, r_idx, c_idx):
    context.user_data["admin_edit_hbtn_coords"] = (r_idx, c_idx)
    grid = get_home_keyboard_grid()
    if r_idx >= len(grid) or c_idx >= len(grid[r_idx]):
        await query.answer("Button not found!")
        await render_home_buttons_panel(query)
        return
        
    btn = grid[r_idx][c_idx]
    status_marker = "🟢 ON (Enabled)" if btn.get("enabled", True) else "🔴 OFF (Disabled)"
    msg = f"""✏️ <b>Modify Home Screen Button</b>
━━━━━━━━━━━━━━━━━━━━
• <b>Text:</b> <code>{html.escape(btn['text'])}</code>
• <b>Action:</b> <code>{html.escape(btn.get('action', ''))}</code>
• <b>Status:</b> {status_marker}
• <b>Position:</b> Row {r_idx+1}, Column {c_idx+1}

Choose what you want to edit:"""

    keyboard = [
        [InlineKeyboardButton("✏️ Edit Text", callback_data="adm_hbtn_edt_text"), InlineKeyboardButton("🔗 Edit Action", callback_data="adm_hbtn_edt_action")],
        [InlineKeyboardButton("🔄 Toggle ON/OFF", callback_data="adm_hbtn_edt_toggle")],
        [InlineKeyboardButton("« Home Buttons Settings", callback_data="adm_home_buttons")]
    ]
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


def check_admin_callback_permission(user_id: int, data: str) -> tuple[bool, str]:
    if db.is_super_admin(user_id):
        return True, ""

    # Course management
    if (data.startswith("adm_course") or data.startswith("adm_dir_") or 
        data.startswith("adm_fld_addcourse") or data.startswith("adm_list_courses") or 
        data.startswith("adm_edit_") or data.startswith("adm_pub_course") or 
        data.startswith("adm_cancel_course") or data.startswith("adm_skip_")):
        if not db.has_permission(user_id, "course_manage"):
            return False, "Course Management"

    # E-Book management
    if (data.startswith("adm_ebook") or data.startswith("adm_ebdir_") or 
        data.startswith("adm_ebfld_addebook") or data.startswith("adm_list_ebooks") or 
        data.startswith("adm_editeb_") or data.startswith("adm_publisheb") or 
        data.startswith("adm_deleb_") or data.startswith("adm_vieweb_") or 
        data.startswith("adm_eb_toggle_") or data.startswith("adm_ebmove_") or 
        data.startswith("adm_addebook_dir_") or data.startswith("adm_skipeb_") or 
        data.startswith("adm_cancel_ebook")):
        if not db.has_permission(user_id, "ebook_manage"):
            return False, "E-Book Management"

    # Category management
    if (data.startswith("adm_categories") or data.startswith("adm_catm_") or 
        data.startswith("adm_subcatm_") or data.startswith("adm_fld_") or 
        data.startswith("adm_ebfld_")):
        if not db.has_permission(user_id, "category_manage"):
            return False, "Category Management"

    # Orders
    if (data.startswith("adm_pending_orders") or data.startswith("adm_all_orders") or 
        data.startswith("adm_porders_") or data.startswith("adm_aorders_") or 
        data.startswith("ordinfo_") or data.startswith("approve_") or 
        data.startswith("reject_") or data.startswith("adm_order_search")):
        if not db.has_permission(user_id, "orders"):
            return False, "Order Management"

    # Coupons & Referral Hub
    if (data.startswith("adm_coupon") or data.startswith("adm_add_coupon") or 
        data.startswith("adm_del_coupon_") or data.startswith("adm_tog_coupon_") or 
        data.startswith("cpnwiz_") or data.startswith("adm_referrals") or 
        data.startswith("adm_tog_referral") or data.startswith("adm_reflist_") or 
        data.startswith("adm_refuview_") or data.startswith("adm_ref_search") or 
        data.startswith("adm_ref_set_bonus")):
        if not (db.has_permission(user_id, "coupon") or db.has_permission(user_id, "user_manage")):
            return False, "Coupon & Referral Management"

    # Payments & Withdrawals
    if (data.startswith("adm_payments") or data.startswith("adm_withdrawals") or 
        data.startswith("adm_paym_") or data.startswith("adm_pedit_") or 
        data.startswith("adm_add_paym") or data.startswith("adm_edit_paynote") or 
        data.startswith("adm_del_paynote") or data.startswith("wdrinfo_") or 
        data.startswith("apprwdr_") or data.startswith("rejwdr_")):
        if not db.has_permission(user_id, "payment_settings"):
            return False, "Payment Settings & Withdrawals"

    # Broadcast
    if (data.startswith("adm_broadcast") or data.startswith("adm_bc_") or 
        data.startswith("bc_sel_")):
        if not db.has_permission(user_id, "broadcast"):
            return False, "Broadcast Notice"

    # Admin Management
    if (data.startswith("adm_admin_list") or data.startswith("adm_add_admin") or 
        data.startswith("adm_rm_admin_menu") or data.startswith("adm_rmadmin_") or 
        data.startswith("adm_makeadmin_")):
        if not db.has_permission(user_id, "admin_manage"):
            return False, "Admin Management"

    # Admin Permission
    if data.startswith("adm_perm_") or data.startswith("adm_togperm_"):
        if not db.has_permission(user_id, "admin_permission"):
            return False, "Admin Permissions"

    # Statistics
    if data.startswith("adm_stats"):
        if not db.has_permission(user_id, "statistics"):
            return False, "Statistics"

    # Bot Settings & Keyboards
    if (data.startswith("adm_bot_settings") or data.startswith("adm_keyboard_settings") or 
        data.startswith("adm_home_buttons") or data.startswith("adm_toggle_maintenance") or 
        data.startswith("adm_toggle_home_btn") or data.startswith("adm_toggle_view_all_courses") or
        data.startswith("adm_editmsg_") or data.startswith("adm_resetmsg_") or 
        data.startswith("adm_kb_") or data.startswith("adm_hbtn_") or data.startswith("adm_info")):
        if not db.has_permission(user_id, "bot_settings"):
            return False, "Bot Settings"

    # User Management
    if (data.startswith("adm_users") or data.startswith("adm_userlist_") or 
        data.startswith("adm_user_search") or data.startswith("adm_uview_") or 
        data.startswith("adm_ubal_") or data.startswith("adm_utogearn_") or 
        data.startswith("adm_ucourse_") or data.startswith("adm_ugrant_") or 
        data.startswith("adm_urevoke_") or data.startswith("adm_udm_")):
        if not (db.has_permission(user_id, "user_manage") or db.has_permission(user_id, "admin_manage") or db.has_permission(user_id, "admin_permission")):
            return False, "User Management"

    return True, ""


async def render_coupon_course_select_page(query, page: int = 1):
    courses = list(db.get_all_courses().items())
    if not courses:
        await query.edit_message_text(
            "❌ No courses available to select.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data="adm_coupons")]])
        )
        return

    total_items = len(courses)
    per_page = 6
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * per_page
    page_courses = courses[start_idx : start_idx + per_page]

    keyboard = []
    for cid, c in page_courses:
        keyboard.append([InlineKeyboardButton(f"{c['name'][:30]}", callback_data=f"cpnwiz_course_{cid}")])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"cpnwiz_scope_course_pg_{page - 1}"))
    if total_pages > 1:
        nav_row.append(InlineKeyboardButton(f"Page {page}/{total_pages}", callback_data="noop_wizcourse_page"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"cpnwiz_scope_course_pg_{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("« Cancel", callback_data="adm_coupons")])
    page_str = f" (Page {page}/{total_pages})" if total_pages > 1 else ""
    await query.edit_message_text(f"🎯 **Choose specific course{page_str}:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def render_admin_coupon_list(query, page: int = 1):
    coupons = db.get_all_coupons()
    if not coupons:
        await query.edit_message_text(
            "❌ No coupons found.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Create Coupon", callback_data="adm_add_coupon_start")],
                [InlineKeyboardButton("« Back", callback_data="adm_coupons")]
            ])
        )
        return

    coupon_items = list(coupons.items())
    total_items = len(coupon_items)
    per_page = 6
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * per_page
    page_coupons = coupon_items[start_idx : start_idx + per_page]

    keyboard = []
    for code, c in page_coupons:
        status_icon = "🟢" if c.get("status", "active") == "active" else "🔴"
        dtype_s = "৳" if c.get("discount_type") == "fixed" else "%"
        dval = c.get("discount_value", c.get("discount", 0))

        if c.get("applicable_course_name"):
            scope_str = f"[{c['applicable_course_name'][:14]}]"
        elif c.get("applicable_category") and c["applicable_category"] != "All":
            scope_str = f"[{c['applicable_category']}]"
        else:
            scope_str = "[All]"

        keyboard.append([
            InlineKeyboardButton(f"{status_icon} {code} ({dval}{dtype_s}) {scope_str}", callback_data=f"adm_view_coupon_{code}")
        ])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"adm_list_coupons_{page - 1}"))
    if total_pages > 1:
        nav_row.append(InlineKeyboardButton(f"Page {page}/{total_pages}", callback_data="noop_coupon_page"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"adm_list_coupons_{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("➕ Create Coupon", callback_data="adm_add_coupon_start")])
    keyboard.append([InlineKeyboardButton("« Back", callback_data="adm_coupons")])

    page_str = f" (Page {page}/{total_pages})" if total_pages > 1 else ""
    await query.edit_message_text(f"📋 **All Coupons List{page_str}:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id

    if data.startswith("noop") or data.endswith("_noop"):
        await query.answer()
        return

    if not is_admin(user_id):
        await query.answer("⛔ Access Denied!", show_alert=True)
        return

    allowed, module_name = check_admin_callback_permission(user_id, data)
    if not allowed:
        await query.answer(f"⛔ Access Denied! You do not have permission for {module_name}.", show_alert=True)
        return

    if data.startswith("bc_sel_"):
        await query.answer()
        parts = data.split("_")
        action = parts[2]
        
        if action == "tgl":
            target_uid = int(parts[3])
            page = int(parts[4])
            
            selected = context.user_data.setdefault("bc_recipients", [])
            if target_uid in selected:
                selected.remove(target_uid)
            else:
                selected.append(target_uid)
            context.user_data["bc_recipients"] = selected
            
            search_query = context.user_data.get("bc_search_query", "")
            if search_query:
                results = db.search_users(search_query)
                await query.edit_message_text(
                    get_broadcast_target_selector_text(context, search_query),
                    parse_mode="Markdown",
                    reply_markup=render_broadcast_target_selector_keyboard(context, search_results=results)
                )
            else:
                await query.edit_message_text(
                    get_broadcast_target_selector_text(context),
                    parse_mode="Markdown",
                    reply_markup=render_broadcast_target_selector_keyboard(context, page=page)
                )
                
        elif action == "pg":
            page = int(parts[3])
            context.user_data.pop("bc_search_query", None)
            await query.edit_message_text(
                get_broadcast_target_selector_text(context),
                parse_mode="Markdown",
                reply_markup=render_broadcast_target_selector_keyboard(context, page=page)
            )
            
        elif action == "search":
            context.user_data["admin_broadcasting_step"] = "search_users_for_bc"
            await query.edit_message_text(
                "🔍 **Search User**\n━━━━━━━━━━━━━━━━━━━━\n\n👇 Send the user's **Name**, **Username**, or **Telegram User ID** to search:\n\n(Click Cancel below to abort search)",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel Search", callback_data="bc_sel_pg_1")]])
            )
            
        elif action == "done":
            selected = context.user_data.get("bc_recipients", [])
            if not selected:
                await query.answer("⚠️ Please select at least 1 user first!", show_alert=True)
                return
            context.user_data.pop("admin_broadcasting_step", None)
            context.user_data.pop("bc_search_query", None)
            await show_broadcast_preview(query, context, user_id)
            
        return

    if data == "adm_home_buttons":
        await render_home_buttons_panel(query)
        return

    elif data == "adm_info_buttons":
        await render_info_buttons_panel(query)
        return

    elif data.startswith("adm_infoitem_"):
        item_key = data.replace("adm_infoitem_", "")
        await render_info_item_editor(query, item_key)
        return

    elif data.startswith("adm_infoedit_"):
        item_key = data.replace("adm_infoedit_", "")
        context.user_data["admin_edit_infoitem_key"] = item_key
        item_label = db.get_info_settings().get("items", {}).get(item_key, {}).get("label") or item_key
        await query.edit_message_text(
            f"✍️ <b>Enter the new description / text for '{html.escape(item_label)}':</b>\n\n"
            f"💡 <i>You can use Markdown or HTML tags. You can also append buttons using <code>[Button Text](URL)</code> at the end of the text.</i>\n\n"
            f"Type /cancel to abort.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data=f"adm_infoitem_{item_key}")]])
        )
        return

    elif data.startswith("adm_inforeset_"):
        item_key = data.replace("adm_inforeset_", "")
        db.reset_info_item(item_key)
        if item_key == "contact":
            db.delete_setting("support_message")
        await query.answer("Reset to default template successfully!", show_alert=True)
        await render_info_item_editor(query, item_key)
        return

    elif data.startswith("adm_infotog_"):
        item_key = data.replace("adm_infotog_", "")
        db.toggle_info_item_status(item_key)
        await query.answer("Status toggled!")
        await render_info_item_editor(query, item_key)
        return

    elif data == "adm_infobtn_add":
        context.user_data["admin_add_infobtn_step"] = "label"
        context.user_data["admin_add_infobtn_data"] = {}
        await query.edit_message_text(
            "✍️ <b>Enter the title/label for the new Info button:</b>\n(e.g., <code>📢 Join Channel</code> or <code>📋 FAQ</code>)\n\nType /cancel to abort.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data="adm_info_buttons")]])
        )
        return

    elif data == "adm_infobtn_del_list":
        cfg = db.get_info_settings()
        c_btns = cfg.get("custom_buttons", [])
        if not c_btns:
            await query.answer("No custom buttons found to delete!", show_alert=True)
            await render_info_buttons_panel(query)
            return
        keyboard = []
        for b in c_btns:
            keyboard.append([InlineKeyboardButton(f"✕ {b['label']}", callback_data=f"adm_infobtn_del_{b['id']}")])
        keyboard.append([InlineKeyboardButton("« Cancel", callback_data="adm_info_buttons")])
        await query.edit_message_text(
            "👇 <b>Select a custom button to delete:</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    elif data.startswith("adm_infobtn_del_"):
        btn_id = data.replace("adm_infobtn_del_", "")
        db.delete_custom_info_button(btn_id)
        await query.answer("Button deleted!")
        await render_info_buttons_panel(query)
        return

    elif data == "adm_hbtn_add":
        context.user_data["admin_add_hbtn_step"] = "text"
        context.user_data["admin_add_hbtn_data"] = {}
        await query.edit_message_text(
            "✍️ <b>Enter the text for the new home button:</b>\n(e.g., <code>📚 Browse Courses</code> or <code>💬 Support</code>)\n\nType /cancel to abort.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data="adm_home_buttons")]])
        )
        return

    elif data == "adm_hbtn_del_list":
        grid = get_home_keyboard_grid()
        keyboard = []
        for r_idx, row in enumerate(grid):
            for c_idx, btn in enumerate(row):
                keyboard.append([InlineKeyboardButton(f"Row {r_idx+1} Col {c_idx+1}: {btn['text']}", callback_data=f"adm_hbtn_del_{r_idx}_{c_idx}")])
        keyboard.append([InlineKeyboardButton("« Cancel", callback_data="adm_home_buttons")])
        await query.edit_message_text(
            "👇 <b>Select a button to delete:</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    elif data.startswith("adm_hbtn_del_"):
        parts = data.replace("adm_hbtn_del_", "").split("_")
        r_idx = int(parts[0])
        c_idx = int(parts[1])
        grid = get_home_keyboard_grid()
        if r_idx < len(grid) and c_idx < len(grid[r_idx]):
            del grid[r_idx][c_idx]
            if not grid[r_idx]:
                del grid[r_idx]
            db.set_setting("home_keyboard_grid", grid)
            await query.answer("🗑️ Button deleted successfully!")
        await render_home_buttons_panel(query)
        return

    elif data == "adm_hbtn_edit_list":
        grid = get_home_keyboard_grid()
        keyboard = []
        for r_idx, row in enumerate(grid):
            for c_idx, btn in enumerate(row):
                keyboard.append([InlineKeyboardButton(f"Row {r_idx+1} Col {c_idx+1}: {btn['text']}", callback_data=f"adm_hbtn_edit_select_{r_idx}_{c_idx}")])
        keyboard.append([InlineKeyboardButton("« Cancel", callback_data="adm_home_buttons")])
        await query.edit_message_text(
            "👇 <b>Select a button to edit:</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    elif data.startswith("adm_hbtn_edit_select_"):
        parts = data.replace("adm_hbtn_edit_select_", "").split("_")
        r_idx = int(parts[0])
        c_idx = int(parts[1])
        await render_hbtn_edit_panel(query, context, r_idx, c_idx)
        return

    elif data == "adm_hbtn_edt_text":
        context.user_data["admin_add_hbtn_step"] = "edit_text"
        await query.edit_message_text(
            "✍️ <b>Enter the new text for this button:</b>\n\nType /cancel to abort.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data="adm_home_buttons")]])
        )
        return

    elif data == "adm_hbtn_edt_action":
        context.user_data["admin_add_hbtn_step"] = "edit_action"
        await query.edit_message_text(
            "✍️ <b>Enter the new action (URL link or callback data):</b>\n\nType /cancel to abort.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data="adm_home_buttons")]])
        )
        return

    elif data == "adm_hbtn_edt_toggle":
        coords = context.user_data.get("admin_edit_hbtn_coords")
        if coords:
            r_idx, c_idx = coords
            grid = get_home_keyboard_grid()
            if r_idx < len(grid) and c_idx < len(grid[r_idx]):
                grid[r_idx][c_idx]["enabled"] = not grid[r_idx][c_idx].get("enabled", True)
                db.set_setting("home_keyboard_grid", grid)
                await query.answer("🔄 Button toggle status updated!")
                await render_hbtn_edit_panel(query, context, r_idx, c_idx)
        return

    elif data.startswith("adm_hbtn_row_"):
        row_choice = data.replace("adm_hbtn_row_", "")
        btn_data = context.user_data.get("admin_add_hbtn_data", {})
        btn_text = btn_data.get("text")
        btn_action = btn_data.get("action")
        
        new_btn = {"text": btn_text, "action": btn_action, "enabled": True}
        grid = get_home_keyboard_grid()
        
        if row_choice == "new_top":
            grid.insert(0, [new_btn])
        elif row_choice == "new_bottom":
            grid.append([new_btn])
        else:
            r_idx = int(row_choice)
            if r_idx < len(grid):
                grid[r_idx].append(new_btn)
                
        db.set_setting("home_keyboard_grid", grid)
        context.user_data.pop("admin_add_hbtn_data", None)
        context.user_data.pop("admin_add_hbtn_step", None)
        await query.answer("✅ Button added successfully!")
        await render_home_buttons_panel(query)
        return

    elif data == "adm_hbtn_resize_prompt":
        keyboard = [
            [InlineKeyboardButton("1 Column (1 Button per Row)", callback_data="adm_hbtn_resize_do_1")],
            [InlineKeyboardButton("2 Columns (2 Buttons per Row)", callback_data="adm_hbtn_resize_do_2")],
            [InlineKeyboardButton("3 Columns (3 Buttons per Row)", callback_data="adm_hbtn_resize_do_3")],
            [InlineKeyboardButton("« Back", callback_data="adm_home_buttons")]
        ]
        await query.edit_message_text(
            "📐 <b>Select column layout format for home buttons:</b>\n\nThis will re-arrange all home buttons automatically into the specified number of columns.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    elif data.startswith("adm_hbtn_resize_do_"):
        cols = int(data.replace("adm_hbtn_resize_do_", ""))
        grid = get_home_keyboard_grid()
        
        flat_buttons = []
        for row in grid:
            for btn in row:
                flat_buttons.append(btn)
                
        new_grid = []
        for i in range(0, len(flat_buttons), cols):
            new_grid.append(flat_buttons[i:i+cols])
            
        db.set_setting("home_keyboard_grid", new_grid)
        await query.answer(f"✅ Layout resized to {cols} columns!")
        await render_home_buttons_panel(query)
        return

    if data == "adm_keyboard_settings":
        await render_keyboard_settings_panel(query)
        return

    elif data == "adm_kb_add":
        context.user_data["admin_add_kb_step"] = "text"
        context.user_data["admin_add_kb_data"] = {}
        await query.edit_message_text(
            "✍️ <b>Enter the text for the new button:</b>\n(Example: <code>📞 Support</code> or <code>📚 Free Courses</code>)\n\nType /cancel to abort.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data="adm_keyboard_settings")]])
        )
        return

    elif data.startswith("adm_kb_act_"):
        act = data.replace("adm_kb_act_", "")
        if act == "menu":
            await query.edit_message_text(
                "👇 <b>Select the action type for this button when clicked:</b>",
                parse_mode="HTML",
                reply_markup=get_action_select_keyboard(is_edit=False)
            )
        elif act == "grp_builtin":
            await query.edit_message_text(
                "👇 <b>Select a built-in action:</b>",
                parse_mode="HTML",
                reply_markup=get_builtin_actions_keyboard(is_edit=False)
            )
        elif act == "grp_cats":
            await query.edit_message_text(
                "👇 <b>Select a course category action:</b>",
                parse_mode="HTML",
                reply_markup=get_categories_actions_keyboard(is_edit=False)
            )
        elif act == "grp_subcats":
            await query.edit_message_text(
                "👇 <b>Select a category to view its subcategories:</b>",
                parse_mode="HTML",
                reply_markup=get_subcat_category_select_keyboard(is_edit=False)
            )
        elif act.startswith("subcatlist_"):
            cat = act.replace("subcatlist_", "")
            await query.edit_message_text(
                f"👇 <b>Select a subcategory in category '{cat}':</b>",
                parse_mode="HTML",
                reply_markup=get_subcategories_actions_keyboard(cat, is_edit=False)
            )
        elif act.startswith("cat_"):
            cat_name = act.replace("cat_", "")
            context.user_data["admin_add_kb_data"]["action"] = f"cat_{cat_name}"
            await prompt_add_kb_row(query, context)
        elif act.startswith("subcat_"):
            subcat_path = act.replace("subcat_", "")
            context.user_data["admin_add_kb_data"]["action"] = f"subcat_{subcat_path}"
            await prompt_add_kb_row(query, context)
        elif act == "custom":
            context.user_data["admin_add_kb_step"] = "custom_action"
            await query.edit_message_text(
                "✍️ <b>Type the custom callback data for this button:</b>\n(e.g., <code>cat_SSC</code> or <code>buy_course1</code> or <code>adm_stats</code>)\n\nType /cancel to abort.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data="adm_keyboard_settings")]])
            )
        else:
            context.user_data["admin_add_kb_data"]["action"] = act
            await prompt_add_kb_row(query, context)
        return

    elif data.startswith("adm_kb_row_"):
        row_choice = data.replace("adm_kb_row_", "")
        context.user_data["admin_add_kb_data"]["row"] = row_choice
        keyboard = [
            [InlineKeyboardButton("👑 Yes (Admins Only)", callback_data="adm_kb_adm_yes")],
            [InlineKeyboardButton("👤 No (All Users)", callback_data="adm_kb_adm_no")],
            [InlineKeyboardButton("« Cancel", callback_data="adm_keyboard_settings")]
        ]
        await query.edit_message_text(
            "👇 <b>Should this button be visible to Admins only?</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    elif data.startswith("adm_kb_adm_"):
        adm_choice = data.replace("adm_kb_adm_", "")
        admin_only = True if adm_choice == "yes" else False
        btn_data = context.user_data.get("admin_add_kb_data", {})
        btn_text = btn_data.get("text")
        btn_action = btn_data.get("action")
        row_sel = btn_data.get("row")
        
        new_btn = {"text": btn_text, "action": btn_action}
        if admin_only:
            new_btn["admin_only"] = True
            
        custom_kb = db.get_custom_keyboards()
        if "buttons" not in custom_kb:
            custom_kb["buttons"] = []
            
        if row_sel == "new_top":
            custom_kb["buttons"].insert(0, [new_btn])
        elif row_sel == "new_bottom" or row_sel == "new":
            custom_kb["buttons"].append([new_btn])
        else:
            try:
                r_idx = int(row_sel)
            except ValueError:
                r_idx = 0
            if r_idx < len(custom_kb["buttons"]):
                custom_kb["buttons"][r_idx].append(new_btn)
            else:
                custom_kb["buttons"].append([new_btn])
                
        db.save_custom_keyboards(custom_kb)
        context.user_data.pop("admin_add_kb_step", None)
        context.user_data.pop("admin_add_kb_data", None)
        await query.answer("✅ Button added successfully!")
        await render_keyboard_settings_panel(query)
        return

    elif data == "adm_kb_del_list":
        custom_kb = db.get_custom_keyboards()
        keyboard = []
        buttons_grid = custom_kb.get("buttons", [])
        for r_idx, r in enumerate(buttons_grid):
            for c_idx, btn in enumerate(r):
                keyboard.append([InlineKeyboardButton(f"✕ Row {r_idx+1}: {btn['text']}", callback_data=f"adm_kb_del_btn_{r_idx}_{c_idx}")])
        keyboard.append([InlineKeyboardButton("« Keyboard Settings", callback_data="adm_keyboard_settings")])
        await query.edit_message_text(
            "👇 <b>Select a button to delete:</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    elif data.startswith("adm_kb_del_btn_"):
        coords = data.replace("adm_kb_del_btn_", "").split("_")
        r_idx = int(coords[0])
        c_idx = int(coords[1])
        custom_kb = db.get_custom_keyboards()
        if "buttons" in custom_kb and r_idx < len(custom_kb["buttons"]):
            row = custom_kb["buttons"][r_idx]
            if c_idx < len(row):
                del row[c_idx]
                if not row:
                    del custom_kb["buttons"][r_idx]
                db.save_custom_keyboards(custom_kb)
                await query.answer("✅ Button deleted successfully!")
        await render_keyboard_settings_panel(query)
        return

    elif data == "adm_kb_edit_list":
        custom_kb = db.get_custom_keyboards()
        keyboard = []
        buttons_grid = custom_kb.get("buttons", [])
        for r_idx, r in enumerate(buttons_grid):
            for c_idx, btn in enumerate(r):
                keyboard.append([InlineKeyboardButton(f"✏️ Row {r_idx+1}: {btn['text']}", callback_data=f"adm_kb_edit_btn_{r_idx}_{c_idx}")])
        keyboard.append([InlineKeyboardButton("« Keyboard Settings", callback_data="adm_keyboard_settings")])
        await query.edit_message_text(
            "👇 <b>Select a button to modify:</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    elif data.startswith("adm_kb_edit_btn_"):
        coords = data.replace("adm_kb_edit_btn_", "").split("_")
        r_idx = int(coords[0])
        c_idx = int(coords[1])
        context.user_data["admin_edit_kb_coords"] = (r_idx, c_idx)
        await render_button_edit_panel(query, context, r_idx, c_idx)
        return

    elif data.startswith("adm_kb_edt_"):
        edt_action = data.replace("adm_kb_edt_", "")
        coords = context.user_data.get("admin_edit_kb_coords")
        if not coords:
            await query.answer("Session expired!")
            await render_keyboard_settings_panel(query)
            return
        r_idx, c_idx = coords
        custom_kb = db.get_custom_keyboards()
        btn = custom_kb["buttons"][r_idx][c_idx]
        
        if edt_action == "text":
            context.user_data["admin_edit_kb_step"] = "text"
            await query.edit_message_text(
                f"✍️ <b>Enter the new text for button '{html.escape(btn['text'])}':</b>\n\nType /cancel to abort.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data=f"adm_kb_edit_btn_{r_idx}_{c_idx}")]])
            )
        elif edt_action == "action":
            await query.edit_message_text(
                "👇 <b>Select the action type for this button:</b>",
                parse_mode="HTML",
                reply_markup=get_action_select_keyboard(is_edit=True, r_idx=r_idx, c_idx=c_idx)
            )
        elif edt_action == "adm":
            btn["admin_only"] = not btn.get("admin_only")
            db.save_custom_keyboards(custom_kb)
            await query.answer("Toggle Admin-Only success!")
            await render_button_edit_panel(query, context, r_idx, c_idx)
        elif edt_action == "move":
            keyboard = [
                [InlineKeyboardButton("⬅️ Move Left", callback_data="adm_kb_edmove_left"), InlineKeyboardButton("➡️ Move Right", callback_data="adm_kb_edmove_right")],
                [InlineKeyboardButton("⬆️ Move Up", callback_data="adm_kb_edmove_up"), InlineKeyboardButton("⬇️ Move Down", callback_data="adm_kb_edmove_down")],
                [InlineKeyboardButton("➕ Move to New Row", callback_data="adm_kb_edmove_new")],
                [InlineKeyboardButton("« Back", callback_data=f"adm_kb_edit_btn_{r_idx}_{c_idx}")]
            ]
            await query.edit_message_text(
                "👇 <b>Choose movement direction:</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return

    elif data.startswith("adm_kb_edact_"):
        act = data.replace("adm_kb_edact_", "")
        coords = context.user_data.get("admin_edit_kb_coords")
        if not coords:
            await query.answer("Session expired!")
            await render_keyboard_settings_panel(query)
            return
        r_idx, c_idx = coords
        custom_kb = db.get_custom_keyboards()

        if act == "menu":
            await query.edit_message_text(
                "👇 <b>Select the action type for this button:</b>",
                parse_mode="HTML",
                reply_markup=get_action_select_keyboard(is_edit=True, r_idx=r_idx, c_idx=c_idx)
            )
        elif act == "grp_builtin":
            await query.edit_message_text(
                "👇 <b>Select a built-in action:</b>",
                parse_mode="HTML",
                reply_markup=get_builtin_actions_keyboard(is_edit=True, r_idx=r_idx, c_idx=c_idx)
            )
        elif act == "grp_cats":
            await query.edit_message_text(
                "👇 <b>Select a course category action:</b>",
                parse_mode="HTML",
                reply_markup=get_categories_actions_keyboard(is_edit=True, r_idx=r_idx, c_idx=c_idx)
            )
        elif act == "grp_subcats":
            await query.edit_message_text(
                "👇 <b>Select a category to view its subcategories:</b>",
                parse_mode="HTML",
                reply_markup=get_subcat_category_select_keyboard(is_edit=True, r_idx=r_idx, c_idx=c_idx)
            )
        elif act.startswith("subcatlist_"):
            cat = act.replace("subcatlist_", "")
            await query.edit_message_text(
                f"👇 <b>Select a subcategory in category '{cat}':</b>",
                parse_mode="HTML",
                reply_markup=get_subcategories_actions_keyboard(cat, is_edit=True, r_idx=r_idx, c_idx=c_idx)
            )
        elif act.startswith("cat_"):
            cat_name = act.replace("cat_", "")
            custom_kb["buttons"][r_idx][c_idx]["action"] = f"cat_{cat_name}"
            db.save_custom_keyboards(custom_kb)
            await query.answer("Action updated to Category!")
            await render_button_edit_panel(query, context, r_idx, c_idx)
        elif act.startswith("subcat_"):
            subcat_path = act.replace("subcat_", "")
            custom_kb["buttons"][r_idx][c_idx]["action"] = f"subcat_{subcat_path}"
            db.save_custom_keyboards(custom_kb)
            await query.answer("Action updated to Sub-category!")
            await render_button_edit_panel(query, context, r_idx, c_idx)
        elif act == "custom":
            context.user_data["admin_edit_kb_step"] = "custom_action"
            await query.edit_message_text(
                "✍️ <b>Type the custom callback data for this button:</b>\n\nType /cancel to abort.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data=f"adm_kb_edit_btn_{r_idx}_{c_idx}")]])
            )
        else:
            custom_kb["buttons"][r_idx][c_idx]["action"] = act
            db.save_custom_keyboards(custom_kb)
            await query.answer("Action modified successfully!")
            await render_button_edit_panel(query, context, r_idx, c_idx)
        return

    elif data.startswith("adm_kb_edmove_"):
        direction = data.replace("adm_kb_edmove_", "")
        coords = context.user_data.get("admin_edit_kb_coords")
        if not coords:
            await query.answer("Session expired!")
            await render_keyboard_settings_panel(query)
            return
        r_idx, c_idx = coords
        custom_kb = db.get_custom_keyboards()
        grid = custom_kb["buttons"]
        btn = grid[r_idx][c_idx]
        if direction == "left":
            if c_idx > 0:
                grid[r_idx][c_idx], grid[r_idx][c_idx - 1] = grid[r_idx][c_idx - 1], grid[r_idx][c_idx]
                c_idx -= 1
                await query.answer("Moved left!")
            else:
                await query.answer("Already at the leftmost position!", show_alert=True)
        elif direction == "right":
            if c_idx < len(grid[r_idx]) - 1:
                grid[r_idx][c_idx], grid[r_idx][c_idx + 1] = grid[r_idx][c_idx + 1], grid[r_idx][c_idx]
                c_idx += 1
                await query.answer("Moved right!")
            else:
                await query.answer("Already at the rightmost position!", show_alert=True)
        elif direction == "up":
            if r_idx > 0:
                del grid[r_idx][c_idx]
                if not grid[r_idx]:
                    del grid[r_idx]
                    r_idx -= 1
                grid[r_idx].append(btn)
                c_idx = len(grid[r_idx]) - 1
                await query.answer("Moved up!")
            else:
                await query.answer("Already at the top row!", show_alert=True)
        elif direction == "down":
            if r_idx < len(grid) - 1:
                del grid[r_idx][c_idx]
                old_empty = False
                if not grid[r_idx]:
                    del grid[r_idx]
                    old_empty = True
                t_idx = r_idx if old_empty else r_idx + 1
                grid[t_idx].append(btn)
                r_idx = t_idx
                c_idx = len(grid[t_idx]) - 1
                await query.answer("Moved down!")
            else:
                await query.answer("Already at the bottom row!", show_alert=True)
        elif direction == "new":
            del grid[r_idx][c_idx]
            if not grid[r_idx]:
                del grid[r_idx]
            grid.append([btn])
            r_idx = len(grid) - 1
            c_idx = 0
            await query.answer("Moved to a new row!")
        db.save_custom_keyboards(custom_kb)
        context.user_data["admin_edit_kb_coords"] = (r_idx, c_idx)
        await render_button_edit_panel(query, context, r_idx, c_idx)
        return

    elif data == "adm_kb_resize_cols_prompt":
        keyboard = [
            [InlineKeyboardButton("1 Column (1 Button per Row)", callback_data="adm_kb_resize_do_1")],
            [InlineKeyboardButton("2 Columns (2 Buttons per Row)", callback_data="adm_kb_resize_do_2")],
            [InlineKeyboardButton("3 Columns (3 Buttons per Row)", callback_data="adm_kb_resize_do_3")],
            [InlineKeyboardButton("« Back", callback_data="adm_keyboard_settings")]
        ]
        await query.edit_message_text(
            "📐 **Select column layout format for your menu:**\n\nThis will re-arrange all existing custom buttons automatically into the specified number of columns.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    elif data.startswith("adm_kb_resize_do_"):
        cols = int(data.replace("adm_kb_resize_do_", ""))
        custom_kb = db.get_custom_keyboards()
        grid = custom_kb.get("buttons", [])
        
        flat_buttons = []
        for row in grid:
            for btn in row:
                flat_buttons.append(btn)
                
        new_grid = []
        for i in range(0, len(flat_buttons), cols):
            new_grid.append(flat_buttons[i:i+cols])
            
        custom_kb["buttons"] = new_grid
        db.save_custom_keyboards(custom_kb)
        await query.answer(f"✅ Keyboard layout resized to {cols} columns!")
        await render_keyboard_settings_panel(query)
        return

    if data == "adm_main":
        stats = db.get_stats()
        maint_status = "🟢 ON" if db.get_setting("maintenance_mode") == True else "🔴 OFF"
        msg = f"""<blockquote>📊 <b>Live Analytics</b>
👥 Students: {stats['total_users']}
📚 Courses: {stats['total_courses']} | 📖 E-Books: {stats.get('total_ebooks', 0)}
📦 Orders: {stats['total_orders']} | ⏳ Pending: <code>{stats['pending_orders']}</code>
💸 Withdrawals: <code>{stats.get('pending_withdrawals', 0)}</code>
💵 Revenue: ৳{stats['total_revenue']} | Paid: ৳{stats.get('total_withdrawn', 0)}
🛠️ **Maintenance Mode:** {maint_status}</blockquote>

<blockquote>👇 <b>Select an option:</b></blockquote>"""

        keyboard = get_admin_dashboard_keyboard(user_id)
        if not keyboard.inline_keyboard:
            await query.answer("⛔ You currently do not have permissions for any admin modules.", show_alert=True)
            return

        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="HTML", reply_markup=keyboard)
        else:
            await query.edit_message_text(msg, parse_mode="HTML", reply_markup=keyboard)

    elif data == "adm_toggle_maintenance":
        current = db.get_setting("maintenance_mode", False)
        new_val = not current
        db.set_setting("maintenance_mode", new_val)
        status_word = "enabled" if new_val else "disabled"
        await query.answer(f"🛠️ Maintenance Mode has been {status_word}!", show_alert=True)
        
        # Re-render admin dashboard
        class FakeCallbackQuery:
            def __init__(self, message, data, user):
                self.message = message
                self.data = data
                self.from_user = user
                self.id = "0"
            async def answer(self, text=None, show_alert=False): pass
            async def edit_message_text(self, text, *args, **kwargs):
                return await self.message.reply_text(text, *args, **kwargs)
        fake_query = FakeCallbackQuery(query.message, "adm_main", query.from_user)
        class FakeUpdate:
            def __init__(self, message, callback_query):
                self.message = message
                self.callback_query = callback_query
                self.effective_user = callback_query.from_user
                self.effective_chat = message.chat
        await handle_admin_callback(FakeUpdate(query.message, fake_query), context)
        return

    elif data == "adm_toggle_home_btn" or data == "adm_toggle_home_btn_kb":
        new_val = db.toggle_home_button()
        status_word = "🟢 ON (Enabled)" if new_val else "🔴 OFF (Disabled)"
        await query.answer(f"⚜️ HOME Button is now {status_word}!", show_alert=True)
        if data == "adm_toggle_home_btn_kb":
            await render_keyboard_settings_panel(query)
        else:
            class FakeCallbackQuery:
                def __init__(self, message, data, user):
                    self.message = message
                    self.data = data
                    self.from_user = user
                    self.id = "0"
                async def answer(self, text=None, show_alert=False): pass
                async def edit_message_text(self, text, *args, **kwargs):
                    return await self.message.reply_text(text, *args, **kwargs)
            fake_query = FakeCallbackQuery(query.message, "adm_bot_settings", query.from_user)
            class FakeUpdate:
                def __init__(self, message, callback_query):
                    self.message = message
                    self.callback_query = callback_query
                    self.effective_user = callback_query.from_user
                    self.effective_chat = message.chat
            await handle_admin_callback(FakeUpdate(query.message, fake_query), context)
        return

    elif data == "adm_toggle_view_all_courses" or data == "adm_toggle_view_all_courses_kb":
        new_val = db.toggle_view_all_courses()
        status_word = "🟢 ON (Enabled)" if new_val else "🔴 OFF (Disabled)"
        await query.answer(f"📚 Browse All Courses Button is now {status_word}!", show_alert=True)
        if data == "adm_toggle_view_all_courses_kb":
            await render_keyboard_settings_panel(query)
        else:
            class FakeCallbackQuery:
                def __init__(self, message, data, user):
                    self.message = message
                    self.data = data
                    self.from_user = user
                    self.id = "0"
                async def answer(self, text=None, show_alert=False): pass
                async def edit_message_text(self, text, *args, **kwargs):
                    return await self.message.reply_text(text, *args, **kwargs)
            fake_query = FakeCallbackQuery(query.message, "adm_bot_settings", query.from_user)
            class FakeUpdate:
                def __init__(self, message, callback_query):
                    self.message = message
                    self.callback_query = callback_query
                    self.effective_user = callback_query.from_user
                    self.effective_chat = message.chat
            await handle_admin_callback(FakeUpdate(query.message, fake_query), context)
        return

    # ==================== BOT SETTINGS ====================
    elif data == "adm_bot_settings":
        def get_status_str(key: str, default_desc: str = "(using default)") -> str:
            val = db.get_setting(key, "")
            if val:
                preview = val[:40].replace("\n", " ") + "..." if len(str(val)) > 40 else val.replace("\n", " ")
                return f"🟢 <b>Customized</b>\n    ↳ <code>{html.escape(preview)}</code>"
            else:
                return f"🔴 <i>{default_desc}</i>"

        home_btn_enabled = db.is_home_button_enabled()
        home_btn_status = "🟢 <b>Enabled (Showing)</b>" if home_btn_enabled else "🔴 <b>Disabled (Hidden)</b>"
        view_all_enabled = db.is_view_all_courses_enabled()
        view_all_status = "🟢 <b>Enabled (Showing)</b>" if view_all_enabled else "🔴 <b>Disabled (Hidden)</b>"
        welcome_status = get_status_str("welcome_message")
        bot_desc_status = get_status_str("bot_description", "Default (Bengali preview)")
        delivery_status = get_status_str("delivery_message")
        maintenance_status = get_status_str("maintenance_message")
        fallback_status = get_status_str("fallback_message")
        info_status = get_status_str("info_message")
        support_status = get_status_str("support_message")

        msg = f"""⚙️ <b>Bot Settings Panel</b>
━━━━━━━━━━━━━━━━━━━━
Here is the status of your bot's automated messages & buttons:

⚜️ <b>Inline HOME Button:</b>
{home_btn_status}

📚 <b>Browse All Courses Button:</b>
{view_all_status}

👋 <b>Start / Welcome Message:</b>
{welcome_status}

🤖 <b>Bot Description (What can this bot do?):</b>
{bot_desc_status}

📦 <b>Delivery Message:</b>
{delivery_status}

🛠️ <b>Maintenance Message:</b>
{maintenance_status}

🔄 <b>Fallback Message:</b>
{fallback_status}

ℹ️ <b>Info / Help Message:</b>
{info_status}

💬 <b>Support / Contact Message:</b>
{support_status}

👇 <b>Select a setting to modify or toggle:</b>"""

        keyboard = [
            [
                InlineKeyboardButton(f"⚜️ HOME: {'ON 🟢' if home_btn_enabled else 'OFF 🔴'}", callback_data="adm_toggle_home_btn"),
                InlineKeyboardButton(f"📚 All Courses: {'ON 🟢' if view_all_enabled else 'OFF 🔴'}", callback_data="adm_toggle_view_all_courses")
            ],
            [
                InlineKeyboardButton("👋 Welcome Msg", callback_data="adm_editmsg_welcome_message"),
                InlineKeyboardButton("🤖 Bot Description", callback_data="adm_editmsg_bot_description")
            ],
            [
                InlineKeyboardButton("📦 Delivery Msg", callback_data="adm_editmsg_delivery_message"),
                InlineKeyboardButton("🛠️ Maint Msg", callback_data="adm_editmsg_maintenance_message")
            ],
            [
                InlineKeyboardButton("🔄 Fallback Msg", callback_data="adm_editmsg_fallback_message"),
                InlineKeyboardButton("💬 Support Msg", callback_data="adm_editmsg_support_message")
            ],
            [
                InlineKeyboardButton("🏡 Home Buttons Settings", callback_data="adm_home_buttons"),
                InlineKeyboardButton("ℹ️ Info Button Settings", callback_data="adm_info_buttons")
            ],
            [
                InlineKeyboardButton("« Back to Admin Menu", callback_data="adm_main")
            ]
        ]

        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "adm_rm_admin_menu":
        admins = db.get_admins()
        removable_admins = [aid for aid in admins if not (len(ADMIN_IDS) > 0 and aid == ADMIN_IDS[0])]

        if not removable_admins:
            await query.answer("কোনো সেকেন্ডারি এডমিন নেই যাকে রিমুভ করা যাবে!", show_alert=True)
            return

        keyboard = []
        for aid in removable_admins:
            u = db.get_user(aid)
            u_name = u.get("full_name") or u.get("username") or str(aid)
            keyboard.append([InlineKeyboardButton(f"✕ Remove: {u_name[:18]} ({aid})", callback_data=f"adm_rmadmin_{aid}")])

        keyboard.append([InlineKeyboardButton("« Back to Admin List", callback_data="adm_admin_list")])

        msg = """<blockquote>✕ <b>[ Remove Admin Role ]</b></blockquote>

<blockquote>⚠️ যাকে এডমিন পদ থেকে অপসারণ করতে চান তার নামের বাটনে চাপুন:</blockquote>"""
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("adm_rmadmin_"):
        target_uid = int(data.replace("adm_rmadmin_", ""))
        removed = db.remove_admin(target_uid)
        if removed:
            try:
                await context.bot.send_message(
                    target_uid,
                    "ℹ️ **Your admin access has been revoked.**\nContact the owner for help.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            await query.answer(f"✅ User {target_uid} removed from admins!", show_alert=True)
        else:
            await query.answer("⛔ Cannot remove the Primary Owner!", show_alert=True)

        admins = db.get_admins()
        admin_lines = []
        for aid in admins:
            u = db.get_user(aid)
            u_name = html.escape(u.get("full_name", "N/A")) if u else f"ID: {aid}"
            u_user = f" (@{html.escape(u.get('username'))})" if (u and u.get('username')) else ""
            is_root = " 👑 <i>[Super Admin / Owner]</i>" if (len(ADMIN_IDS) > 0 and aid == ADMIN_IDS[0]) else " 🛡️ <i>[Admin]</i>"
            admin_lines.append(f"• <b>{u_name}</b>{u_user} — <code>{aid}</code>{is_root}")

        list_text = "\n".join(admin_lines) if admin_lines else "<i>কোনো এডমিন পাওয়া যায়নি</i>"
        msg = f"""<blockquote>👑 <b>Admin Team & Role Management</b></blockquote>

<blockquote>📋 <b>বর্তমান এডমিন তালিকা ({len(admins)} জন):</b>
{list_text}</blockquote>

<blockquote>💡 নতুন এডমিন যুক্ত করতে নিচের <b>➕ Add New Admin</b> বাটনে চাপুন।</blockquote>"""

        keyboard = []
        for aid in admins:
            u = db.get_user(aid)
            u_name = u.get("full_name") or u.get("username") or str(aid)
            keyboard.append([InlineKeyboardButton(f"👤 {u_name[:20]} (ID: {aid})", callback_data=f"adm_uview_{aid}")])

        keyboard.append([
            InlineKeyboardButton("➕ Add New Admin", callback_data="adm_add_admin"),
            InlineKeyboardButton("✕ Remove Admin", callback_data="adm_rm_admin_menu")
        ])
        keyboard.append([InlineKeyboardButton("« User Management", callback_data="adm_users")])

        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("adm_editmsg_"):
        msg_key = data.replace("adm_editmsg_", "")
        
        if msg_key == "delivery_message":
            def get_status_str_short(key: str, default_val: str) -> str:
                val = db.get_setting(key, "")
                if not val:
                    val = default_val
                preview = val[:80].replace("\n", " ") + "..." if len(str(val)) > 80 else val.replace("\n", " ")
                return f"<code>{html.escape(preview)}</code>"

            # Defaults for the 4 types
            df_free_wl = "🎉 **Your Course Access is Confirmed!**\n\n**Course:** {course_name}\n\n👇 Click the button below to join your course:\n{access_text}"
            df_free_nl = "🎉 **Your Course Access is Confirmed!**\n\n**Course:** {course_name}\n\n⚠️ No direct link is currently available. Please contact admin."
            df_paid_wl = "🎉 **Your Course Purchase was Successful!**\n\n**Course:** {course_name}\n**Order ID:** `{order_id}`\n\n✅ **Payment completed successfully.**\n\n👇 Click the button below to join your course:\n{access_text}"
            df_paid_nl = "🎉 **Your Course Purchase was Successful!**\n\n**Course:** {course_name}\n**Order ID:** `{order_id}`\n\n✅ **Payment completed successfully.**\n\n⚠️ No direct link is attached to this course. Please contact support."

            msg = f"""📦 <b>Delivery Text Settings</b>
━━━━━━━━━━━━━━━━━━━━

🆕 <b>Free Course — With Link:</b>
{get_status_str_short("delivery_free_with_link", df_free_wl)}

🆕 <b>Free Course — No Link:</b>
{get_status_str_short("delivery_free_no_link", df_free_nl)}

💳 <b>Paid Course — With Link:</b>
{get_status_str_short("delivery_paid_with_link", df_paid_wl)}

💳 <b>Paid Course — No Link:</b>
{get_status_str_short("delivery_paid_no_link", df_paid_nl)}"""

            keyboard = [
                [InlineKeyboardButton("✏️ 🆕 Free Course — With Link", callback_data="adm_editmsg_delivery_free_with_link")],
                [InlineKeyboardButton("✏️ 🆕 Free Course — No Link", callback_data="adm_editmsg_delivery_free_no_link")],
                [InlineKeyboardButton("✏️ 💳 Paid Course — With Link", callback_data="adm_editmsg_delivery_paid_with_link")],
                [InlineKeyboardButton("✏️ 💳 Paid Course — No Link", callback_data="adm_editmsg_delivery_paid_no_link")],
                [InlineKeyboardButton("◀️ Back", callback_data="adm_bot_settings")]
            ]
            await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
            return

        context.user_data["admin_msg_edit_key"] = msg_key
        current_val = db.get_setting(msg_key, "")

        display_key = msg_key.replace("_", " ").title()
        if current_val:
            preview = f"📝 <b>Current:</b>\n<blockquote>{html.escape(current_val[:500])}</blockquote>"
        else:
            preview = "📝 <b>Current:</b> <i>(Using default message)</i>"

        if msg_key == "info_message":
            cancel_data = "adm_info_buttons"
        elif msg_key.startswith("delivery_"):
            cancel_data = "adm_editmsg_delivery_message"
        else:
            cancel_data = "adm_bot_settings"
        edit_kb = [
            [
                InlineKeyboardButton("« Cancel", callback_data=cancel_data)
            ]
        ]
        if current_val:
            edit_kb[0].append(InlineKeyboardButton("🗑️ Reset to Default", callback_data=f"adm_resetmsg_{msg_key}"))

        help_text = ""
        if msg_key.startswith("delivery_"):
            help_text = "\n💡 <i>Supported placeholders:</i> <code>{course_name}</code> (or <code>${courseName}</code>), <code>{order_id}</code>, <code>{amount}</code>, <code>{access_text}</code>\n"

        await query.edit_message_text(
            f"✏️ <b>Edit {display_key}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{preview}\n{help_text}\n👇 <b>Send the new message:</b>\n(Or use the buttons below to abort or reset)",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(edit_kb)
        )

    elif data.startswith("adm_resetmsg_"):
        msg_key = data.replace("adm_resetmsg_", "")
        db.delete_setting(msg_key)
        
        # If it was bot_description, update on Telegram
        if msg_key == "bot_description":
            try:
                await context.bot.set_my_description(description=DEFAULT_BOT_DESCRIPTION)
            except Exception as e:
                logger.error(f"Failed to reset bot description on Telegram: {e}")
                
        display_key = msg_key.replace("_", " ").title()
        await query.answer(f"✅ Reset {display_key} to default!", show_alert=True)
        
        # Re-render bot settings or delivery settings
        class FakeCallbackQuery:
            def __init__(self, message, data, user):
                self.message = message
                self.data = data
                self.from_user = user
                self.id = "0"
            async def answer(self, text=None, show_alert=False): pass
            async def edit_message_text(self, text, *args, **kwargs):
                return await self.message.reply_text(text, *args, **kwargs)
        if msg_key == "info_message":
            fake_data = "adm_info_buttons"
        elif msg_key.startswith("delivery_"):
            fake_data = "adm_editmsg_delivery_message"
        else:
            fake_data = "adm_bot_settings"
        fake_query = FakeCallbackQuery(query.message, fake_data, query.from_user)
        class FakeUpdate:
            def __init__(self, message, callback_query):
                self.message = message
                self.callback_query = callback_query
                self.effective_user = callback_query.from_user
                self.effective_chat = message.chat
        await handle_admin_callback(FakeUpdate(query.message, fake_query), context)
        return

    # ==================== PAYMENT SETTINGS ====================
    elif data == "adm_payments":
        methods = db.get_payment_methods()
        note = db.get_payment_note()

        msg = f"""⚙ <b>Payment Settings Management</b>
━━━━━━━━━━━━━━━━━━━━

📋 <b>Active Payment Methods ({len(methods)}):</b>"""

        keyboard = []
        for m in methods:
            st_icon = "🟢" if m.get("status") == "active" else "🔴"
            msg += f"\n• {st_icon} <b>{m['name']}:</b> <code>{m['number']}</code> ({html.escape(m.get('instruction', 'Personal'))})"
            keyboard.append([
                InlineKeyboardButton(f"⚙ Manage: {m['name']} ({m['number']})", callback_data=f"adm_paym_{m['key']}")
            ])

        note_display = html.escape(note) if note else "<i>(None)</i>"
        msg += f"\n\n📝 <b>Current Payment Disclaimer / Notes:</b>\n{note_display}"

        keyboard.append([InlineKeyboardButton("➕ Add Payment Method", callback_data="adm_add_paym")])
        keyboard.append([InlineKeyboardButton("📝 Edit Payment Notes", callback_data="adm_edit_paynote"), InlineKeyboardButton("🗑️ Clear Note", callback_data="adm_del_paynote")])
        keyboard.append([InlineKeyboardButton("« Admin Menu", callback_data="adm_main")])

        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("adm_paym_"):
        pkey = data.replace("adm_paym_", "")
        m = db.get_payment_method(pkey)
        if not m:
            await query.answer("Payment method not found!")
            return

        st_text = "Active 🟢" if m.get("status") == "active" else "Inactive 🔴"
        msg = f"""💳 <b>{html.escape(m['name'])} Settings</b>
━━━━━━━━━━━━━━━━━━━━

📱 <b>Number:</b> <code>{html.escape(m['number'])}</code>
⚙ <b>Instruction:</b> {html.escape(m.get('instruction', 'Personal'))}
📌 <b>Status:</b> {st_text}

Select an action:"""

        keyboard = [
            [InlineKeyboardButton("📱 Change Number", callback_data=f"adm_pedit_{pkey}_num"), InlineKeyboardButton("⚙ Change Instruction", callback_data=f"adm_pedit_{pkey}_ins")],
            [InlineKeyboardButton("🔄 Toggle Status", callback_data=f"adm_pedit_{pkey}_toggle"), InlineKeyboardButton("✕ Delete Method", callback_data=f"adm_pedit_{pkey}_del")],
            [InlineKeyboardButton("« Payment Settings", callback_data="adm_payments")]
        ]
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("adm_pedit_"):
        rest = data.replace("adm_pedit_", "", 1)
        parts = rest.rsplit("_", 1)
        pkey = parts[0]
        action = parts[1] if len(parts) > 1 else ""

        if action == "toggle":
            db.toggle_payment_method_status(pkey)
            await query.answer("Status updated!")
            m = db.get_payment_method(pkey)
            st_text = "Active 🟢" if m.get("status") == "active" else "Inactive 🔴"
            msg = f"""💳 <b>{html.escape(m['name'])} Settings</b>
━━━━━━━━━━━━━━━━━━━━

📱 <b>Number:</b> <code>{html.escape(m['number'])}</code>
⚙ <b>Instruction:</b> {html.escape(m.get('instruction', 'Personal'))}
📌 <b>Status:</b> {st_text}

Select an action:"""
            keyboard = [
                [InlineKeyboardButton("📱 Change Number", callback_data=f"adm_pedit_{pkey}_num"), InlineKeyboardButton("⚙ Change Instruction", callback_data=f"adm_pedit_{pkey}_ins")],
                [InlineKeyboardButton("🔄 Toggle Status", callback_data=f"adm_pedit_{pkey}_toggle"), InlineKeyboardButton("✕ Delete Method", callback_data=f"adm_pedit_{pkey}_del")],
                [InlineKeyboardButton("« Payment Settings", callback_data="adm_payments")]
            ]
            await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

        elif action == "del":
            db.delete_payment_method(pkey)
            await query.answer("Payment method deleted!", show_alert=True)
            methods = db.get_payment_methods()
            note = db.get_payment_note()
            msg = f"""⚙ <b>Payment Settings Management</b>
━━━━━━━━━━━━━━━━━━━━

📋 <b>Active Payment Methods ({len(methods)}):</b>"""
            keyboard = []
            for item in methods:
                st_icon = "🟢" if item.get("status") == "active" else "🔴"
                msg += f"\n• {st_icon} <b>{html.escape(item['name'])}:</b> <code>{html.escape(item['number'])}</code>"
                keyboard.append([InlineKeyboardButton(f"⚙ Manage: {item['name']} ({item['number']})", callback_data=f"adm_paym_{item['key']}")])
            keyboard.append([InlineKeyboardButton("➕ Add Payment Method", callback_data="adm_add_paym")])
            keyboard.append([InlineKeyboardButton("📝 Edit Payment Notes", callback_data="adm_edit_paynote"), InlineKeyboardButton("🗑️ Clear Note", callback_data="adm_del_paynote")])
            keyboard.append([InlineKeyboardButton("« Admin Menu", callback_data="adm_main")])
            await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

        elif action == "num":
            context.user_data["admin_pay_step"] = "edit_num"
            context.user_data["admin_pay_target_key"] = pkey
            await query.edit_message_text(f"📱 <b>Enter new number for {html.escape(pkey.title())}:</b>\n\n(Type /cancel to abort)", parse_mode="HTML")

        elif action == "ins":
            context.user_data["admin_pay_step"] = "edit_ins"
            context.user_data["admin_pay_target_key"] = pkey
            await query.edit_message_text(f"⚙ <b>Enter new instruction for {html.escape(pkey.title())} (e.g. Personal / Agent / Merchant):</b>\n\n(Type /cancel to abort)", parse_mode="HTML")

    elif data == "adm_add_paym":
        context.user_data["admin_pay_step"] = "add_name"
        context.user_data["new_pay_method"] = {}
        await query.edit_message_text("💳 <b>Enter Payment Method Name (e.g. Upay or City Bank):</b>\n\n(Type /cancel to abort)", parse_mode="HTML")

    elif data == "adm_edit_paynote":
        context.user_data["admin_pay_step"] = "edit_note"
        cur_note = db.get_payment_note()
        await query.edit_message_text(
            f"""📝 <b>Edit Payment Disclaimer / Notes:</b>
━━━━━━━━━━━━━━━━━━━━━

Current Note:
<blockquote>{html.escape(cur_note) if cur_note else '<i>(None)</i>'}</blockquote>

👇 <b>Send the new payment disclaimer note:</b>
(Type /cancel to abort)""",
            parse_mode="HTML"
        )

    elif data == "adm_del_paynote":
        db.set_payment_note("")
        await query.answer("🗑️ Payment note cleared!", show_alert=True)
        methods = db.get_payment_methods()
        note = db.get_payment_note()
        msg = f"""⚙ <b>Payment Settings Management</b>
━━━━━━━━━━━━━━━━━━━━━

📋 <b>Active Payment Methods ({len(methods)}):</b>"""
        keyboard = []
        for item in methods:
            st_icon = "🟢" if item.get("status") == "active" else "🔴"
            msg += f"\n• {st_icon} <b>{html.escape(item['name'])}:</b> <code>{html.escape(item['number'])}</code>"
            keyboard.append([InlineKeyboardButton(f"⚙ Manage: {item['name']} ({item['number']})", callback_data=f"adm_paym_{item['key']}")])
        keyboard.append([InlineKeyboardButton("➕ Add Payment Method", callback_data="adm_add_paym")])
        keyboard.append([InlineKeyboardButton("📝 Edit Payment Notes", callback_data="adm_edit_paynote"), InlineKeyboardButton("🗑️ Clear Note", callback_data="adm_del_paynote")])
        keyboard.append([InlineKeyboardButton("« Admin Menu", callback_data="adm_main")])
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    # ==================== USER MANAGEMENT ====================
    # ==================== USER & ADMIN MANAGEMENT ====================
    elif data == "adm_users":
        total_u = len(db.users)
        admin_list = db.get_admins()
        msg = f"""<blockquote>👥 <b>User & Admin Management</b></blockquote>

<blockquote>📊 <b>Overview:</b>
👤 <b>Total Users:</b> <code>{total_u}</code> 
👑 <b>Total Admins:</b> <code>{len(admin_list)}</code> </blockquote>"""

        keyboard = [
            [InlineKeyboardButton("📋 User List", callback_data="adm_userlist_1"),
             InlineKeyboardButton("🔍 Search User", callback_data="adm_user_search")],
            [InlineKeyboardButton("👑 Admin List & Manage", callback_data="adm_admin_list"),
             InlineKeyboardButton("➕ Add Admin", callback_data="adm_add_admin")],
            [InlineKeyboardButton("📢 Broadcast Notice", callback_data="adm_broadcast")],
            [InlineKeyboardButton("« Admin Menu", callback_data="adm_main")]
        ]
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "adm_admin_list":
        admins = db.get_admins()
        admin_lines = []
        for aid in admins:
            u = db.get_user(aid)
            u_name = html.escape(u.get("full_name", "N/A")) if u else f"ID: {aid}"
            u_user = f" (@{html.escape(u.get('username'))})" if (u and u.get('username')) else ""
            is_root = " 👑 <i>[Super Admin / Owner]</i>" if (len(ADMIN_IDS) > 0 and aid == ADMIN_IDS[0]) else " 🛡️ <i>[Admin]</i>"
            admin_lines.append(f"• <b>{u_name}</b>{u_user} — <code>{aid}</code>{is_root}")

        list_text = "\n".join(admin_lines) if admin_lines else "<i>কোনো এডমিন পাওয়া যায়নি</i>"

        msg = f"""<blockquote>👑 <b>Admin Team & Role Management</b></blockquote>

<blockquote>📋 <b>বর্তমান এডমিন তালিকা ({len(admins)} জন):</b>
{list_text}</blockquote>

<blockquote>💡 এডমিনের নামের উপর চাপ দিয়ে <b>পারমিশন পরিবর্তন (Permission Toggle)</b> অথবা <b>রিমুভ</b> করুন।</blockquote>"""

        keyboard = []
        for aid in admins:
            u = db.get_user(aid)
            u_name = u.get("full_name") or u.get("username") or str(aid)
            is_root = (len(ADMIN_IDS) > 0 and aid == ADMIN_IDS[0])
            badge = "👑 Super Admin" if is_root else "🔐 Permissions"
            keyboard.append([InlineKeyboardButton(f"👤 {u_name[:18]} ({badge})", callback_data=f"adm_perm_{aid}")])

        keyboard.append([
            InlineKeyboardButton("➕ Add New Admin", callback_data="adm_add_admin"),
            InlineKeyboardButton("✕ Remove Admin", callback_data="adm_rm_admin_menu")
        ])
        keyboard.append([InlineKeyboardButton("« User Management", callback_data="adm_users")])

        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("adm_perm_"):
        target_uid = int(data.replace("adm_perm_", ""))
        await render_admin_permission_dashboard(query, context, target_uid)
        return

    elif data.startswith("adm_togperm_"):
        parts = data.replace("adm_togperm_", "").split("_", 1)
        target_uid = int(parts[0])
        p_key = parts[1]

        if not db.has_permission(user_id, "admin_permission"):
            await query.answer("⛔ Access Denied! You do not have permission to manage permissions.", show_alert=True)
            return

        if db.is_super_admin(target_uid):
            await query.answer("👑 Super Admin permissions cannot be modified!", show_alert=True)
            return

        new_val = db.toggle_admin_permission(target_uid, p_key)
        p_name = ADMIN_PERMISSION_DEFINITIONS.get(p_key, {}).get("name", p_key)
        status_txt = "Granted ✅" if new_val else "Revoked ❌"
        await query.answer(f"{p_name}: {status_txt}")
        await render_admin_permission_dashboard(query, context, target_uid)
        return

    elif data.startswith("adm_rmadmin_"):
        target_uid = int(data.replace("adm_rmadmin_", ""))
        if not db.has_permission(user_id, "admin_manage"):
            await query.answer("⛔ Access Denied! You do not have permission to remove admins.", show_alert=True)
            return

        if db.is_super_admin(target_uid):
            await query.answer("👑 Super Admin cannot be removed!", show_alert=True)
            return

        removed = db.remove_admin(target_uid)
        if removed:
            try:
                await context.bot.send_message(
                    target_uid,
                    "⚠️ **আপনার এডমিন রোল অপসারণ করা হয়েছে।**",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            await query.answer(f"✅ Admin {target_uid} removed successfully!", show_alert=True)
        else:
            await query.answer("ℹ️ User is not an admin!", show_alert=True)

        # Refresh admin list
        admins = db.get_admins()
        admin_lines = []
        for aid in admins:
            u = db.get_user(aid)
            u_name = html.escape(u.get("full_name", "N/A")) if u else f"ID: {aid}"
            u_user = f" (@{html.escape(u.get('username'))})" if (u and u.get('username')) else ""
            is_root = " 👑 <i>[Super Admin / Owner]</i>" if (len(ADMIN_IDS) > 0 and aid == ADMIN_IDS[0]) else " 🛡️ <i>[Admin]</i>"
            admin_lines.append(f"• <b>{u_name}</b>{u_user} — <code>{aid}</code>{is_root}")

        list_text = "\n".join(admin_lines) if admin_lines else "<i>কোনো এডমিন পাওয়া যায়নি</i>"

        msg = f"""<blockquote>👑 <b>Admin Team & Role Management</b></blockquote>

<blockquote>📋 <b>বর্তমান এডমিন তালিকা ({len(admins)} জন):</b>
{list_text}</blockquote>

<blockquote>💡 এডমিনের নামের উপর চাপ দিয়ে <b>পারমিশন পরিবর্তন (Permission Toggle)</b> অথবা <b>রিমুভ</b> করুন।</blockquote>"""

        keyboard = []
        for aid in admins:
            u = db.get_user(aid)
            u_name = u.get("full_name") or u.get("username") or str(aid)
            is_root = (len(ADMIN_IDS) > 0 and aid == ADMIN_IDS[0])
            badge = "👑 Super Admin" if is_root else "🔐 Permissions"
            keyboard.append([InlineKeyboardButton(f"👤 {u_name[:18]} ({badge})", callback_data=f"adm_perm_{aid}")])

        keyboard.append([
            InlineKeyboardButton("➕ Add New Admin", callback_data="adm_add_admin"),
            InlineKeyboardButton("✕ Remove Admin", callback_data="adm_rm_admin_menu")
        ])
        keyboard.append([InlineKeyboardButton("« User Management", callback_data="adm_users")])

        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data == "adm_rm_admin_menu":
        admins = db.get_admins()
        removable_admins = [aid for aid in admins if not (len(ADMIN_IDS) > 0 and aid == ADMIN_IDS[0])]

        if not removable_admins:
            await query.answer("No secondary admins to remove!", show_alert=True)
            return

        keyboard = []
        for aid in removable_admins:
            u = db.get_user(aid)
            u_name = u.get("full_name") or u.get("username") or str(aid)
            keyboard.append([InlineKeyboardButton(f"✕ Remove: {u_name[:18]} ({aid})", callback_data=f"adm_rmadmin_{aid}")])

        keyboard.append([InlineKeyboardButton("« Back to Admin List", callback_data="adm_admin_list")])

        msg = """<blockquote>✕ <b>[ Remove Admin Role ]</b></blockquote>

<blockquote>⚠️ Select the admin you want to remove below:</blockquote>"""
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "adm_bc_create":
        context.user_data["admin_broadcasting_mode"] = "create"
        context.user_data["admin_broadcasting_step"] = "msg_content"
        context.user_data["bc_payload"] = {}
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=user_id,
            text="""✍ **Send your Broadcast Message:**
━━━━━━━━━━━━━━━━━━━━

You can send Text, Photo, Video, or Document.

💡 Click Cancel below to abort.""",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([["❌ Cancel"]], resize_keyboard=True)
        )
        return

    elif data == "adm_bc_forward":
        context.user_data["admin_broadcasting_mode"] = "forward"
        context.user_data["admin_broadcasting_step"] = "fwd_content"
        context.user_data["bc_payload"] = {}
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=user_id,
            text="""✍ **Forward your Broadcast Message:**
━━━━━━━━━━━━━━━━━━━━

Forward any message from a channel or chat to broadcast it.

💡 Click Cancel below to abort.""",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([["❌ Cancel"]], resize_keyboard=True)
        )
        return

    elif data == "adm_bc_target_all":
        context.user_data["bc_target_mode"] = "all"
        context.user_data["bc_recipients"] = db.get_all_user_ids()
        await show_broadcast_preview(query, context, user_id)

    elif data == "adm_bc_target_sel":
        context.user_data["bc_target_mode"] = "selected"
        context.user_data["bc_recipients"] = []
        context.user_data["admin_broadcasting_step"] = "sel_uids"
        try:
            await query.message.delete()
        except Exception:
            pass
        
        reply_markup = render_broadcast_target_selector_keyboard(context, page=1)
        text = get_broadcast_target_selector_text(context)
        
        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        return

    elif data == "adm_bc_confirm_send":
        await execute_broadcast(query, context, user_id)

    elif data == "adm_bc_cancel":
        context.user_data.pop("admin_broadcasting_mode", None)
        context.user_data.pop("admin_broadcasting_step", None)
        context.user_data.pop("bc_payload", None)
        context.user_data.pop("bc_recipients", None)
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=user_id,
            text="✕ Broadcast cancelled.",
            reply_markup=main_menu_keyboard(user_id)
        )
        return

    elif data == "adm_add_admin":
        context.user_data["admin_user_step"] = "add_admin"
        await query.edit_message_text(
            """<blockquote>👑 <b>[ ➕ Add New Admin ]</b></blockquote>

<blockquote>📝 <b>নির্দেশনা:</b>
যাকে এডমিন বানাতে চান তার <b>Telegram User ID</b> অথবা <b>Username</b> লিখে পাঠান।
(যেমন: <code>7610279126</code> বা <code>username</code>)

💡 <i>ইউজারকে অবশ্যই বটটিতে অন্তত একবার স্টার্ট (/start) করা থাকতে হবে।</i></blockquote>

<blockquote>❌ বাতিল করতে <code>/cancel</code> লিখুন।</blockquote>""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data="adm_admin_list")]])
        )

    elif data == "adm_rm_admin_menu":
        admins = db.get_admins()
        removable_admins = [aid for aid in admins if not (len(ADMIN_IDS) > 0 and aid == ADMIN_IDS[0])]

        if not removable_admins:
            await query.answer("No secondary admins to remove!", show_alert=True)
            return

        keyboard = []
        for aid in removable_admins:
            u = db.get_user(aid)
            u_name = u.get("full_name") or u.get("username") or str(aid)
            keyboard.append([InlineKeyboardButton(f"✕ Remove: {u_name[:18]} ({aid})", callback_data=f"adm_rmadmin_{aid}")])

        keyboard.append([InlineKeyboardButton("« Back to Admin List", callback_data="adm_admin_list")])

        msg = """<blockquote>✕ <b>[ Remove Admin Role ]</b></blockquote>

<blockquote>⚠️ Select the admin you want to remove below:</blockquote>"""
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("adm_makeadmin_"):
        target_uid = int(data.replace("adm_makeadmin_", ""))
        added = db.add_admin(target_uid, added_by=user_id)
        if added:
            try:
                await context.bot.send_message(
                    target_uid,
                    "🎉 **অভিনন্দন! আপনাকে StudyMart বট-এর এডমিন হিসেবে যুক্ত করা হয়েছে।**\n\n👉 /admin কমান্ড দিয়ে আপনি এডমিন প্যানেল অ্যাক্সেস করতে পারবেন।",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            await query.answer(f"✅ User {target_uid} promoted to Admin!", show_alert=True)
        else:
            await query.answer("ℹ️ User already an admin!", show_alert=True)

        # Return to user view
        u = db.get_user(target_uid)
        c_list = u.get("purchased_courses", []) if u else []
        c_names = [db.get_course(cid)["name"] if db.get_course(cid) else cid for cid in c_list]
        courses_str = "\n".join([f"  • {cn}" for cn in c_names]) if c_names else "  (None)"
        orders = db.get_user_orders(target_uid)
        total_spent = sum(o.get("amount", 0) for o in orders if o.get("status") == "approved")
        earn_st = "Enabled ✅" if (u and u.get("earnings_enabled")) else "Disabled ❌"
        is_user_adm = db.is_admin(target_uid)
        is_root_owner = (len(ADMIN_IDS) > 0 and target_uid == ADMIN_IDS[0])
        adm_role_str = "👑 Super Admin" if is_root_owner else ("🛡️ Admin" if is_user_adm else "👤 Student")

        msg = f"""<blockquote>👤 <b>User Profile Details</b></blockquote>

<blockquote>🆔 <b>User ID:</b> <code>{target_uid}</code>
👤 <b>Name:</b> {html.escape(u.get('full_name', 'N/A') if u else 'N/A')}
🔹 <b>Username:</b> @{html.escape(u.get('username') or 'N/A' if u else 'N/A')}
👑 <b>Role:</b> <b>{adm_role_str}</b>
📅 <b>Join Date:</b> {(u.get('joined_date', 'N/A') if u else 'N/A')[:19]}
💰 <b>Wallet Balance:</b> <code>{u.get('balance', 0) if u else 0}</code> ৳
📊 <b>Earnings Option:</b> {earn_st}
💳 <b>Total Spent:</b> {total_spent} ৳</blockquote>

<blockquote>📚 <b>Enrolled Courses ({len(c_list)}):</b>
{html.escape(courses_str)}</blockquote>"""

        reply_markup = get_admin_uview_keyboard(target_uid, is_user_adm, is_root_owner)
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=reply_markup)

    elif data.startswith("adm_userlist_"):
        page_num = int(data.replace("adm_userlist_", ""))
        users_page, total_pages = db.get_paginated_users(page=page_num, per_page=6)

        msg = f"📋 **Student List (Page {page_num}/{total_pages}):**\n━━━━━━━━━━━━━━━━━━━━\nSelect a user to view profile and manage access:\n"

        keyboard = []
        for u in users_page:
            u_name = u.get("full_name") or u.get("username") or str(u.get("user_id"))
            courses_c = len(u.get("purchased_courses", []))
            is_adm_badge = " 👑" if db.is_admin(u['user_id']) else ""
            keyboard.append([
                InlineKeyboardButton(f"{u_name[:20]}{is_adm_badge} (📚 {courses_c})", callback_data=f"adm_uview_{u['user_id']}")
            ])

        nav_row = []
        if page_num > 1:
            nav_row.append(InlineKeyboardButton("◀️", callback_data=f"adm_userlist_{page_num-1}"))
        else:
            nav_row.append(InlineKeyboardButton("⏹️", callback_data="adm_ignore"))

        nav_row.append(InlineKeyboardButton("🔍 Search", callback_data="adm_user_search"))

        if page_num < total_pages:
            nav_row.append(InlineKeyboardButton("▶️", callback_data=f"adm_userlist_{page_num+1}"))
        else:
            nav_row.append(InlineKeyboardButton("⏹️", callback_data="adm_ignore"))

        keyboard.append(nav_row)
        keyboard.append([InlineKeyboardButton("« User Menu", callback_data="adm_users")])

        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "adm_user_search":
        context.user_data["admin_user_step"] = "search"
        await query.edit_message_text(
            """<blockquote>🔍 <b>Search User / শিক্ষার্থী খুঁজুন</b></blockquote>

<blockquote>📝 যাকে খুঁজতে চান তার <b>Telegram User ID</b>, <b>Username</b> অথবা <b>Name</b> লিখে পাঠান।
(যেমন: <code>7610279126</code> বা <code>student</code>)</blockquote>

<blockquote>💡 বাতিল করতে নিচের <b>« Cancel</b> বাটনে চাপুন অথবা <code>/cancel</code> লিখুন।</blockquote>""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data="adm_users")]])
        )

    elif data == "adm_order_search":
        context.user_data["admin_order_search_step"] = True
        await query.edit_message_text(
            """<blockquote>🔍 <b>Search Orders / অর্ডার খুঁজুন</b></blockquote>

<blockquote>📝 <b>Order ID</b>, <b>TrxID</b>, <b>User ID</b> অথবা <b>Student Name</b> লিখে পাঠান।
(যেমন: <code>1001</code> বা <code>TX12345</code>)</blockquote>

<blockquote>💡 বাতিল করতে নিচের <b>« Cancel</b> বাটনে চাপুন অথবা <code>/cancel</code> লিখুন।</blockquote>""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data="adm_pending_orders")]])
        )

    elif data.startswith("adm_uview_"):
        uid = int(data.replace("adm_uview_", ""))
        u = db.get_user(uid)
        if not u:
            await query.answer("User not found!")
            return

        c_list = u.get("purchased_courses", [])
        c_names = []
        for cid in c_list:
            c_obj = db.get_course(cid)
            c_names.append(c_obj["name"] if c_obj else cid)
        courses_str = "\n".join([f"  • {cn}" for cn in c_names]) if c_names else "  (None)"

        orders = db.get_user_orders(uid)
        total_spent = sum(o.get("amount", 0) for o in orders if o.get("status") == "approved")
        earn_st = "Enabled ✅" if u.get("earnings_enabled") else "Disabled ❌"
        is_user_adm = db.is_admin(uid)
        is_root_owner = (len(ADMIN_IDS) > 0 and uid == ADMIN_IDS[0])
        adm_role_str = "👑 Super Admin" if is_root_owner else ("🛡️ Admin" if is_user_adm else "👤 Student")

        msg = f"""<blockquote>👤 <b>User Profile Details</b></blockquote>

<blockquote>🆔 <b>User ID:</b> <code>{u['user_id']}</code>
👤 <b>Name:</b> {html.escape(u.get('full_name', 'N/A'))}
🔹 <b>Username:</b> @{html.escape(u.get('username') or 'N/A')}
👑 <b>Role:</b> <b>{adm_role_str}</b>
📅 <b>Join Date:</b> {u.get('joined_date', 'N/A')[:19]}
💰 <b>Wallet Balance:</b> <code>{u.get('balance', 0)}</code> ৳
📊 <b>Earnings Option:</b> {earn_st}
💳 <b>Total Spent:</b> {total_spent} ৳</blockquote>

<blockquote>📚 <b>Enrolled Courses ({len(c_list)}):</b>
{html.escape(courses_str)}</blockquote>"""

        reply_markup = get_admin_uview_keyboard(uid, is_user_adm, is_root_owner)
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=reply_markup)

    elif data.startswith("adm_ubal_"):
        uid = int(data.replace("adm_ubal_", ""))
        context.user_data["admin_user_step"] = f"adjust_bal_{uid}"
        await query.edit_message_text(
            f"""<blockquote>💰 <b>Adjust Student Wallet Balance</b></blockquote>

<blockquote>👤 <b>User ID:</b> <code>{uid}</code>
💵 <b>বর্তমান ব্যালেন্স:</b> <code>{db.get_user(uid).get('balance', 0) if db.get_user(uid) else 0}</code> ৳</blockquote>

<blockquote>👇 <b>নতুন ব্যালেন্সটি টাইপ করে পাঠান (যেমন: 500):</b></blockquote>

❌ বাতিল করতে <code>/cancel</code> লিখুন।""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data=f"adm_uview_{uid}")]])
        )

    elif data.startswith("adm_utogearn_"):
        uid = int(data.replace("adm_utogearn_", ""))
        u = db.get_user(uid)
        if u:
            cur = u.get("earnings_enabled", False)
            db.enable_user_earnings(uid, not cur)
            await query.answer("Earnings menu toggled!", show_alert=True)
            u = db.get_user(uid)
            c_list = u.get("purchased_courses", [])
            c_names = []
            for cid in c_list:
                c_obj = db.get_course(cid)
                c_names.append(c_obj["name"] if c_obj else cid)
            courses_str = "\n".join([f"  • {cn}" for cn in c_names]) if c_names else "  (None)"
            orders = db.get_user_orders(uid)
            total_spent = sum(o.get("amount", 0) for o in orders if o.get("status") == "approved")
            earn_st = "Enabled ✅" if u.get("earnings_enabled") else "Disabled ❌"
            is_user_adm = db.is_admin(uid)
            is_root_owner = (len(ADMIN_IDS) > 0 and uid == ADMIN_IDS[0])
            adm_role_str = "👑 Super Admin" if is_root_owner else ("🛡️ Admin" if is_user_adm else "👤 Student")

            msg = f"""<blockquote>👤 <b>User Profile Details</b></blockquote>

<blockquote>🆔 <b>User ID:</b> <code>{u['user_id']}</code>
👤 <b>Name:</b> {html.escape(u.get('full_name', 'N/A'))}
🔹 <b>Username:</b> @{html.escape(u.get('username') or 'N/A')}
👑 <b>Role:</b> <b>{adm_role_str}</b>
📅 <b>Join Date:</b> {u.get('joined_date', 'N/A')[:19]}
💰 <b>Wallet Balance:</b> <code>{u.get('balance', 0)}</code> ৳
📊 <b>Earnings Option:</b> {earn_st}
💳 <b>Total Spent:</b> {total_spent} ৳</blockquote>

<blockquote>📚 <b>Enrolled Courses ({len(c_list)}):</b>
{html.escape(courses_str)}</blockquote>"""
            reply_markup = get_admin_uview_keyboard(uid, is_user_adm, is_root_owner)
            await query.edit_message_text(msg, parse_mode="HTML", reply_markup=reply_markup)

    elif data.startswith("adm_ucourse_"):
        uid = int(data.replace("adm_ucourse_", ""))
        u = db.get_user(uid)
        if not u:
            return

        all_courses = db.get_all_courses()
        user_c = u.get("purchased_courses", [])

        msg = f"🎓 **User `{uid}` Course Access Management:**\n━━━━━━━━━━━━━━━━━━━━\nClick to Grant or Revoke access:\n"

        keyboard = []
        for cid, c in all_courses.items():
            if cid in user_c:
                keyboard.append([InlineKeyboardButton(f"✕ Revoke: {c['name'][:18]}", callback_data=f"adm_urevoke_{uid}_{cid}")])
            else:
                keyboard.append([InlineKeyboardButton(f"➕ Grant: {c['name'][:18]}", callback_data=f"adm_ugrant_{uid}_{cid}")])

        keyboard.append([InlineKeyboardButton("« User Profile", callback_data=f"adm_uview_{uid}")])
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("adm_ugrant_"):
        parts = data.split("_")
        uid = int(parts[2])
        cid = parts[3]
        db.add_purchase(uid, cid)
        await query.answer("✅ Course access granted!", show_alert=True)
        all_courses = db.get_all_courses()
        u = db.get_user(uid)
        user_c = u.get("purchased_courses", [])
        msg = f"🎓 **User `{uid}` Course Access Management:**\n━━━━━━━━━━━━━━━━━━━━\nClick to Grant or Revoke access:\n"
        keyboard = []
        for c_id, c in all_courses.items():
            if c_id in user_c:
                keyboard.append([InlineKeyboardButton(f"✕ Revoke: {c['name'][:18]}", callback_data=f"adm_urevoke_{uid}_{c_id}")])
            else:
                keyboard.append([InlineKeyboardButton(f"➕ Grant: {c['name'][:18]}", callback_data=f"adm_ugrant_{uid}_{c_id}")])
        keyboard.append([InlineKeyboardButton("« User Profile", callback_data=f"adm_uview_{uid}")])
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("adm_urevoke_"):
        parts = data.split("_")
        uid = int(parts[2])
        cid = parts[3]
        db.manual_revoke_course(uid, cid)
        await query.answer("✕ Course access revoked!", show_alert=True)
        all_courses = db.get_all_courses()
        u = db.get_user(uid)
        user_c = u.get("purchased_courses", [])
        msg = f"🎓 **User `{uid}` Course Access Management:**\n━━━━━━━━━━━━━━━━━━━━\nClick to Grant or Revoke access:\n"
        keyboard = []
        for c_id, c in all_courses.items():
            if c_id in user_c:
                keyboard.append([InlineKeyboardButton(f"✕ Revoke: {c['name'][:18]}", callback_data=f"adm_urevoke_{uid}_{c_id}")])
            else:
                keyboard.append([InlineKeyboardButton(f"➕ Grant: {c['name'][:18]}", callback_data=f"adm_ugrant_{uid}_{c_id}")])
        keyboard.append([InlineKeyboardButton("« User Profile", callback_data=f"adm_uview_{uid}")])
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("adm_udm_"):
        uid = int(data.replace("adm_udm_", ""))
        context.user_data["admin_user_step"] = "send_dm"
        context.user_data["admin_dm_target_uid"] = uid
        await query.edit_message_text(f"➥ **Type message to send directly to User `{uid}`:**\n\n(Type /cancel to abort)")

    # ==================== BROADCAST SYSTEM ====================
    elif data == "adm_broadcast":
        keyboard = [
            [InlineKeyboardButton("✍ Create Message", callback_data="adm_bc_create")],
            [InlineKeyboardButton("➥ Forward Message", callback_data="adm_bc_forward")],
            [InlineKeyboardButton("« Admin Menu", callback_data="adm_main")]
        ]
        text = """⚡ **Broadcast System**
━━━━━━━━━━━━━━━━━━━━

Choose how you want to broadcast:

1️⃣ **Create Message:** Send text, photo, video or document with optional custom button/link.
2️⃣ **Forward Message:** Forward any existing Telegram post directly to all/selected users."""
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))





    # ==================== WITHDRAWAL MANAGEMENT ====================
    elif data == "adm_withdrawals":
        pending_w = db.get_pending_withdrawals()
        if not pending_w:
            await query.edit_message_text(
                "✅ No pending withdrawal requests currently!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Admin Menu", callback_data="adm_main")]])
            )
            return

        keyboard = []
        for w in pending_w[:10]:
            keyboard.append([
                InlineKeyboardButton(
                    f"💳 {w['amount']}৳ - {w.get('method', '')} ({w.get('account', '')[:8]}..)",
                    callback_data=f"wdrinfo_{w['withdraw_id']}"
                )
            ])
        keyboard.append([InlineKeyboardButton("« Admin Menu", callback_data="adm_main")])

        await query.edit_message_text(
            f"💸 **Pending Withdrawal Requests ({len(pending_w)}):**\nSelect to review and process:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("wdrinfo_"):
        wid = data.replace("wdrinfo_", "")
        w = db.get_withdrawal(wid)
        if not w:
            await query.answer("Withdrawal request not found!")
            return

        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"apprwdr_{wid}"),
                InlineKeyboardButton("✕ Reject & Refund", callback_data=f"rejwdr_{wid}")
            ],
            [InlineKeyboardButton("« Withdraw List", callback_data="adm_withdrawals")]
        ]

        await query.edit_message_text(
            f"""💸 **Withdrawal Request Details**
━━━━━━━━━━━━━━━━━━━━

🆔 **Withdraw ID:** `{wid}`
👤 **User:** {w.get('full_name', 'N/A')} (@{w.get('username', 'N/A')})
🆔 **User ID:** `{w.get('user_id')}`
💰 **Amount:** `{w['amount']}` ৳
💳 **Method:** {w.get('method')}
📱 **Account:** `{w.get('account')}`
📅 **Date:** {w.get('date', 'N/A')[:19]}
📌 **Status:** {w.get('status', '').upper()}""",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("apprwdr_"):
        wid = data.replace("apprwdr_", "")
        w = db.get_withdrawal(wid)
        if w and w.get("status") == "pending":
            db.approve_withdrawal(wid)
            try:
                await context.bot.send_message(
                    w["user_id"],
                    f"""🎉 **আলহামদুলিল্লাহ! আপনার উইথড্রয়াল অনুমোদিত হয়েছে!**
━━━━━━━━━━━━━━━━━━━━

🆔 **উইথড্র আইডি:** `{wid}`
💰 **টাকার পরিমাণ:** {w['amount']} ৳
💳 **মাধ্যম:** {w.get('method')} ({w.get('account')})

✨ নির্দিষ্ট একাউন্টে টাকা পাঠিয়ে দেওয়া হয়েছে। সাথে থাকার জন্য ধন্যবাদ!""",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"উইথড্র নোটিফিকেশন পাঠাতে সমস্যা: {e}")

            await query.edit_message_text(
                f"✅ **Withdrawal `{wid}` approved successfully!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Withdraw List", callback_data="adm_withdrawals")]])
            )

    elif data.startswith("rejwdr_"):
        wid = data.replace("rejwdr_", "")
        w = db.get_withdrawal(wid)
        if w and w.get("status") == "pending":
            db.reject_withdrawal(wid)
            try:
                await context.bot.send_message(
                    w["user_id"],
                    f"""❌ **দুঃখিত! আপনার উইথড্রয়াল রিকোয়েস্টটি বাতিল করা হয়েছে।**
━━━━━━━━━━━━━━━━━━━━

🆔 **উইথড্র আইডি:** `{wid}`
💰 **টাকার পরিমাণ:** {w['amount']} ৳

🔄 **{w['amount']} ৳ আপনার ওয়ালেট ব্যালেন্সে পুনরায় রিফান্ড করা হয়েছে।**
সহায়তার জন্য এডমিনের সাথে যোগাযোগ করুন: @{SUPPORT_USERNAME}""",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"উইথড্র রিজেক্ট নোটিশ পাঠাতে সমস্যা: {e}")

            await query.edit_message_text(
                f"❌ **Withdrawal `{wid}` rejected and refunded to user balance!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Withdraw List", callback_data="adm_withdrawals")]])
            )

    # ==================== CATEGORY & SUB-CATEGORY MANAGEMENT (FOLDER DIRECTORY) ====================
    elif data == "adm_categories":
        await render_admin_folder_directory(query, context, "")

    elif data.startswith("adm_dir_"):
        path = data.replace("adm_dir_", "")
        await render_admin_folder_directory(query, context, path)

    elif data.startswith("adm_catm_"):
        cat = data.replace("adm_catm_", "")
        await render_admin_folder_directory(query, context, cat)

    elif data.startswith("adm_subcatm_"):
        parts = data.replace("adm_subcatm_", "").split("_", 1)
        path = f"{parts[0]} > {parts[1]}" if len(parts) > 1 else parts[0]
        await render_admin_folder_directory(query, context, path)

    elif data == "adm_fld_addfolder" or data == "adm_add_cat":
        active_dir = context.user_data.get("active_dir", "")
        context.user_data["admin_add_subcat"] = True
        context.user_data["admin_subcat_target_parent"] = active_dir
        parent_display = active_dir if active_dir else "Root (মূল ক্যাটাগরি)"
        
        msg = f"""📁 **ফোল্ডার অবস্থান:** `{parent_display}`
━━━━━━━━━━━━━━━━━━━━
➕ **নতুন ফোল্ডারের নাম লিখে পাঠান:**
(যেমন: Physics, Chemistry, Cycle 1, Academic ইত্যাদি)

(বাতিল করতে /cancel লিখুন)"""
        k_c = [[InlineKeyboardButton("« Cancel", callback_data=f"adm_dir_{active_dir}")]]
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_c))
        else:
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_c))

    elif data.startswith("adm_addsub_"):
        cat = data.replace("adm_addsub_", "")
        context.user_data["admin_add_subcat"] = True
        context.user_data["admin_subcat_target_parent"] = cat
        await query.edit_message_text(f"➕ **Enter new Sub-Category / Folder name for `{cat}`:**\n\n(Type /cancel to abort)")

    elif data == "adm_fld_addcourse":
        active_dir = context.user_data.get("active_dir", "")
        segments = [s.strip() for s in active_dir.split(">") if s.strip()]
        cat = segments[0] if segments else "General"
        sub = " > ".join(segments[1:]) if len(segments) > 1 else "General"

        context.user_data["admin_add_course"] = True
        context.user_data["course_step"] = "name"
        context.user_data["course_origin_callback"] = f"adm_dir_{active_dir}"
        context.user_data["new_course"] = {
            "category": cat,
            "subcategory": sub,
            "folder_path": active_dir,
            "program": segments[-1].lower() if segments else "general"
        }

        path_disp = active_dir if active_dir else "General"
        msg = f"""📁 **ডিরেক্টরি: `{path_disp}`**
━━━━━━━━━━━━━━━━━━━━

✨ **ধাপ ১/৪: কোর্সের নাম (Course Name) লিখে পাঠান:**
(যেমন: BH Biology Full Course | {cat})

💡 ক্যাটাগরি ও ফোল্ডার স্বয়ংক্রিয়ভাবে `{path_disp}` ডিরেক্টরিতে সেট করা থাকবে।"""
        k_cancel = [[InlineKeyboardButton("✕ Cancel", callback_data=f"adm_dir_{active_dir}")]]
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cancel))
        else:
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cancel))

    elif data.startswith("adm_addcourse_dir_") or data.startswith("adm_addcourse_sub_"):
        if data.startswith("adm_addcourse_dir_"):
            target_path = data.replace("adm_addcourse_dir_", "")
        else:
            parts = data.replace("adm_addcourse_sub_", "").split("_", 1)
            target_path = f"{parts[0]} > {parts[1]}" if len(parts) > 1 else parts[0]

        segments = [s.strip() for s in target_path.split(">") if s.strip()]
        cat = segments[0] if segments else "General"
        sub = " > ".join(segments[1:]) if len(segments) > 1 else "General"

        context.user_data["active_dir"] = target_path
        context.user_data["admin_add_course"] = True
        context.user_data["course_step"] = "name"
        context.user_data["new_course"] = {
            "category": cat,
            "subcategory": sub,
            "folder_path": target_path,
            "program": segments[-1].lower() if segments else "general"
        }

        msg = f"""📁 **ডিরেক্টরি: `{target_path}`**
━━━━━━━━━━━━━━━━━━━━

✨ **ধাপ ১/৪: কোর্সের নাম (Course Name) লিখে পাঠান:**
(যেমন: BH Biology Full Course | {cat})

💡 ক্যাটাগরি ও ফোল্ডার স্বয়ংক্রিয়ভাবে `{target_path}` ডিরেক্টরিতে সেট করা থাকবে।"""
        k_cancel = [[InlineKeyboardButton("✕ Cancel", callback_data=f"adm_dir_{target_path}")]]
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cancel))
        else:
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cancel))

    elif data.startswith("adm_addcourse_cat_"):
        cat = data.replace("adm_addcourse_cat_", "")
        context.user_data["active_dir"] = cat
        context.user_data["admin_add_course"] = True
        context.user_data["course_step"] = "name"
        context.user_data["new_course"] = {
            "category": cat,
            "subcategory": "General",
            "folder_path": cat,
            "program": "general"
        }

        msg = f"""📁 **ডিরেক্টরি: `{cat}`**
━━━━━━━━━━━━━━━━━━━━

✨ **ধাপ ১/৪: কোর্সের নাম (Course Name) লিখে পাঠান:**
(যেমন: BH Biology Full Course | {cat})

💡 ক্যাটাগরি ও ফোল্ডার স্বয়ংক্রিয়ভাবে `{cat}` ডিরেক্টরিতে সেট করা থাকবে।"""
        k_cancel = [[InlineKeyboardButton("✕ Cancel", callback_data=f"adm_dir_{cat}")]]

        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cancel))
        else:
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cancel))

    elif data.startswith("adm_fld_togglestatus_"):
        active_dir = context.user_data.get("active_dir", "")
        if not active_dir:
            await query.answer("Cannot toggle root directory status!", show_alert=True)
            return
        new_st = db.toggle_category_status(active_dir)
        st_word = "🟢 Active (ON)" if new_st else "🔴 Inactive (OFF - Hidden)"
        await query.answer(f"Category status changed to {st_word}!", show_alert=True)
        await render_admin_folder_directory(query, context, active_dir)
        return

    elif data == "adm_fld_delfolder":
        active_dir = context.user_data.get("active_dir", "")
        if not active_dir:
            await query.answer("Cannot delete root directory!", show_alert=True)
            return

        segments = [s.strip() for s in active_dir.split(">") if s.strip()]
        folder_to_delete = segments[-1]
        parent_path = " > ".join(segments[:-1]) if len(segments) > 1 else ""

        db.delete_sub_folder(parent_path, folder_to_delete)
        await query.answer(f"✅ '{folder_to_delete}' deleted!", show_alert=True)
        await render_admin_folder_directory(query, context, parent_path)

    elif data.startswith("adm_fld_moveup_") or data.startswith("adm_fld_movedown_"):
        direction = "up" if "moveup" in data else "down"
        folder_name = data.replace("adm_fld_moveup_", "").replace("adm_fld_movedown_", "")
        active_dir = context.user_data.get("active_dir", "")
        segments = [s.strip() for s in active_dir.split(">") if s.strip()]
        parent_path = " > ".join(segments[:-1]) if len(segments) > 1 else ""
        success = db.move_folder_order(parent_path, folder_name, direction)
        if success:
            siblings = db.get_sub_folders(parent_path, include_inactive=True)
            pos = siblings.index(folder_name) + 1 if folder_name in siblings else "?"
            await query.answer(f"✅ '{folder_name}' moved {direction}! (Position: #{pos} of {len(siblings)})", show_alert=True)
        else:
            await query.answer(f"⚠️ '{folder_name}' is already at the {'top' if direction == 'up' else 'bottom'}!", show_alert=True)
        await render_admin_folder_directory(query, context, active_dir)

    elif data.startswith("adm_fld_reorder_"):
        p_path = data.replace("adm_fld_reorder_", "")
        context.user_data["reorder_parent_path"] = p_path
        await render_category_reorder_panel(query, context, p_path)
        return

    elif data.startswith("adm_fld_reord_"):
        parts = data.replace("adm_fld_reord_", "").split("_")
        action = parts[0]
        idx = int(parts[1])
        p_path = context.user_data.get("reorder_parent_path", "")
        folders = db.get_sub_folders(p_path, include_inactive=True)
        if 0 <= idx < len(folders):
            f_name = folders[idx]
            direction = "up" if action == "up" else "down"
            ok = db.move_folder_order(p_path, f_name, direction)
            if ok:
                await query.answer(f"✅ '{f_name}' moved {direction}!")
            else:
                await query.answer(f"⚠️ Cannot move {direction} further!")
        await render_category_reorder_panel(query, context, p_path)
        return

    elif data.startswith("adm_fld_mleft_"):
        active_dir = context.user_data.get("active_dir", "")
        segments = [s.strip() for s in active_dir.split(">") if s.strip()]
        if len(segments) <= 1:
            await query.answer("⚠️ Already a root category, cannot move left!", show_alert=True)
            return
        current_name = segments[-1]
        parent_path = " > ".join(segments[:-1])
        
        success = db.move_folder_left(parent_path, current_name)
        if success:
            parent_segments = parent_path.split(" > ")
            parent_parent = " > ".join(parent_segments[:-1]) if len(parent_segments) > 1 else ""
            new_active_dir = f"{parent_parent} > {current_name}" if parent_parent else current_name
            await query.answer("✅ Moved left successfully!", show_alert=True)
            await render_admin_folder_directory(query, context, new_active_dir)
        else:
            await query.answer("❌ Move left failed!", show_alert=True)

    elif data.startswith("adm_fld_mright_"):
        active_dir = context.user_data.get("active_dir", "")
        segments = [s.strip() for s in active_dir.split(">") if s.strip()]
        if not segments:
            await query.answer("Cannot move!", show_alert=True)
            return
        current_name = segments[-1]
        parent_path = " > ".join(segments[:-1]) if len(segments) > 1 else ""
        
        siblings = [s for s in db.get_sub_folders(parent_path) if s != current_name]
        if not siblings:
            await query.answer("⚠️ No sibling folders to move into!", show_alert=True)
            return
            
        context.user_data["move_folder_name"] = current_name
        context.user_data["move_folder_parent"] = parent_path
        context.user_data["move_folder_siblings"] = siblings
        
        keyboard = []
        for idx, s in enumerate(siblings):
            keyboard.append([InlineKeyboardButton(f"📁 {s}", callback_data=f"adm_fld_doright_{idx}")])
        keyboard.append([InlineKeyboardButton("« Cancel", callback_data=f"adm_dir_{active_dir}")])
        
        await query.edit_message_text(
            f"➡️ <b>Select a sibling folder to move '{html.escape(current_name)}' into:</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("adm_fld_doright_"):
        idx = int(data.replace("adm_fld_doright_", ""))
        current_name = context.user_data.get("move_folder_name")
        parent_path = context.user_data.get("move_folder_parent")
        siblings = context.user_data.get("move_folder_siblings", [])
        
        if not current_name or idx >= len(siblings):
            await query.answer("Session expired or invalid choice!", show_alert=True)
            return
            
        sibling_name = siblings[idx]
        success = db.move_folder_right(parent_path, current_name, sibling_name)
        
        context.user_data.pop("move_folder_name", None)
        context.user_data.pop("move_folder_parent", None)
        context.user_data.pop("move_folder_siblings", None)
        
        if success:
            await query.answer(f"✅ Moved '{current_name}' into '{sibling_name}'!", show_alert=True)
            await render_admin_folder_directory(query, context, parent_path)
        else:
            await query.answer("❌ Move failed!", show_alert=True)

    elif data.startswith("adm_fld_rename_"):
        folder_name = data.replace("adm_fld_rename_", "")
        active_dir = context.user_data.get("active_dir", "")
        segments = [s.strip() for s in active_dir.split(">") if s.strip()]
        parent_path = " > ".join(segments[:-1]) if len(segments) > 1 else ""
        context.user_data["rename_folder_old"] = folder_name
        context.user_data["rename_folder_parent"] = parent_path
        context.user_data["awaiting_folder_rename"] = True
        await query.edit_message_text(
            f"✏️ **Rename '{folder_name}' to:**\n\n(Type new name or click Cancel below)",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data=f"adm_dir_{active_dir}")]])
        )

    elif data.startswith("adm_delcat_"):
        cat_name = data.replace("adm_delcat_", "")
        db.delete_category(cat_name)
        await query.answer(f"✅ '{cat_name}' deleted!", show_alert=True)
        await render_admin_folder_directory(query, context, "")

    elif data.startswith("adm_delsubmenu_"):
        cat = data.replace("adm_delsubmenu_", "")
        subcats = db.get_sub_folders(cat)
        if not subcats:
            await query.answer("No sub-categories available!")
            return

        keyboard = []
        for s in subcats:
            keyboard.append([InlineKeyboardButton(f"✕ Delete: {s}", callback_data=f"adm_delsub_{cat}_{s}")])
        keyboard.append([InlineKeyboardButton("« Back", callback_data=f"adm_dir_{cat}")])

        await query.edit_message_text(f"✕ **Select sub-category to delete from `{cat}`:**", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("adm_delsub_"):
        parts = data.replace("adm_delsub_", "").split("_", 1)
        cat = parts[0]
        sub = parts[1]
        db.delete_sub_folder(cat, sub)
        await query.answer(f"✅ '{sub}' deleted!", show_alert=True)
        await render_admin_folder_directory(query, context, cat)

    # ==================== COURSE MANAGEMENT ====================
    elif data == "adm_courses":
        keyboard = [
            [InlineKeyboardButton("📁 Manage Course Categories", callback_data="adm_categories")],
            [InlineKeyboardButton("📋 All Courses List", callback_data="adm_list_courses")],
            [InlineKeyboardButton("➕ Add New Course", callback_data="adm_add_course")],
            [InlineKeyboardButton("« Admin Menu", callback_data="adm_main")]
        ]
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text("❖ **Course Management Menu:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text("❖ **Course Management Menu:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "adm_add_course":
        active_dir = context.user_data.get("active_dir", "")
        if active_dir:
            segments = [s.strip() for s in active_dir.split(">") if s.strip()]
            cat = segments[0] if segments else "General"
            sub = " > ".join(segments[1:]) if len(segments) > 1 else "General"

            context.user_data["admin_add_course"] = True
            context.user_data["course_step"] = "name"
            context.user_data["course_origin_callback"] = f"adm_dir_{active_dir}"
            context.user_data["new_course"] = {
                "category": cat,
                "subcategory": sub,
                "folder_path": active_dir,
                "program": segments[-1].lower() if segments else "general"
            }

            path_disp = active_dir
            msg = f"""📁 **ডিরেক্টরি: `{path_disp}`**
━━━━━━━━━━━━━━━━━━━━

✨ **ধাপ ১/৪: কোর্সের নাম (Course Name) লিখে পাঠান:**
(যেমন: BH Biology Full Course | {cat})

💡 ক্যাটাগরি ও ফোল্ডার স্বয়ংক্রিয়ভাবে `{path_disp}` ডিরেক্টরিতে সেট করা থাকবে।"""
            k_cancel = [[InlineKeyboardButton("✕ Cancel", callback_data=f"adm_dir_{active_dir}")]]
            if query.message.photo:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cancel))
            else:
                await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cancel))
        else:
            categories = db.get_categories()
            if not categories:
                await query.answer("⚠️ প্রথমে একটি ক্যাটাগরি তৈরি করুন!", show_alert=True)
                return
            keyboard = []
            row = []
            for cat in categories:
                row.append(InlineKeyboardButton(f"📁 {cat}", callback_data=f"adm_addcourse_dir_{cat}"))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)
            keyboard.append([InlineKeyboardButton("« Cancel", callback_data="adm_courses")])
            
            msg = """📂 **কোর্স যোগ করতে প্রথমে ক্যাটাগরি নির্বাচন করুন:**
━━━━━━━━━━━━━━━━━━━━
যে ক্যাটাগরি বা ফোল্ডারের অধীনে নতুন কোর্সটি যুক্ত করতে চান সেটি নির্বাচন করুন:"""
            if query.message.photo:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("adm_setcat_"):
        cat_selected = data.replace("adm_setcat_", "")
        if cat_selected == "CUSTOM":
            context.user_data["course_step"] = "type_custom_cat"
            msg_p = "✍️ **নতুন ক্যাটাগরির নাম লিখুন (যেমন: HSC 28 বা SSC):**\n\n(বাতিল করতে নিচের বাটনে চাপুন)"
            k_canc = [[InlineKeyboardButton("« Cancel", callback_data="adm_courses")]]
            if query.message.photo:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await query.message.reply_text(msg_p, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_canc))
            else:
                await query.edit_message_text(msg_p, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_canc))
            return

        new_course = context.user_data.get("new_course", {})
        new_course["category"] = cat_selected
        context.user_data["new_course"] = new_course
        context.user_data["course_step"] = "subcategory"
        await send_admin_subcategory_selector(query, context, cat_selected)

    elif data.startswith("adm_setsub_"):
        sub_chosen = data.replace("adm_setsub_", "")
        new_course = context.user_data.get("new_course", {})

        if sub_chosen == "CUSTOM":
            context.user_data["course_step"] = "type_custom_sub"
            msg_p = "✍️ **নতুন সাব-ক্যাটাগরির নাম লিখুন (যেমন: Academic বা Physics):**\n\n(বাতিল করতে নিচের বাটনে চাপুন)"
            k_canc = [[InlineKeyboardButton("« Cancel", callback_data="adm_courses")]]
            if query.message.photo:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await query.message.reply_text(msg_p, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_canc))
            else:
                await query.edit_message_text(msg_p, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_canc))
            return

        new_course["subcategory"] = sub_chosen
        new_course["program"] = sub_chosen.lower()
        context.user_data["new_course"] = new_course

        # If name, price, description are all filled, go to link/image or preview
        if new_course.get("name") and ("price" in new_course) and new_course.get("description"):
            if "access_link" not in new_course:
                context.user_data["course_step"] = "link"
                p_text = """🔗 **ধাপ ৬/৭: টেলিগ্রাম প্রাইভেট চ্যানেল বা ড্রাইভ এক্সেস লিংক পাঠান:**
━━━━━━━━━━━━━━━━━━━━
💡 লিংক না থাকলে বা পরে দিতে চাইলে নিচের স্কিপ বাটনে চাপুন:"""
                k_link = [
                    [InlineKeyboardButton("⏭️ Skip Link (স্কিপ করুন)", callback_data="adm_skip_link")],
                    [InlineKeyboardButton("✕ Cancel", callback_data="adm_cancel_course")]
                ]
                if query.message.photo:
                    try:
                        await query.message.delete()
                    except Exception:
                        pass
                    await query.message.reply_text(p_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_link))
                else:
                    await query.edit_message_text(p_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_link))
            elif not new_course.get("image"):
                context.user_data["course_step"] = "image"
                p_img = """🖼️ **ধাপ ৭/৭: কোর্সের ব্যানার বা ছবি পাঠান:**
━━━━━━━━━━━━━━━━━━━━
💡 ছবি ছাড়া প্রকাশ করতে চাইলে নিচের স্কিপ বাটনে চাপুন:"""
                k_img = [
                    [InlineKeyboardButton("⏭️ Skip Image (ছবি ছাড়া প্রকাশ)", callback_data="adm_skip_img")],
                    [InlineKeyboardButton("✕ Cancel", callback_data="adm_cancel_course")]
                ]
                if query.message.photo:
                    try:
                        await query.message.delete()
                    except Exception:
                        pass
                    await query.message.reply_text(p_img, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_img))
                else:
                    await query.edit_message_text(p_img, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_img))
            else:
                await send_admin_course_preview(query, context, new_course)
        else:
            context.user_data["course_step"] = "price"
            p_price = f"📖 **Sub-Category:** `{sub_chosen}`\n\n💰 **ধাপ ৪/৬: কোর্সের মূল্য লিখুন (Price in BDT):**\n(ফ্রি কোর্সের জন্য `0` লিখুন)"
            k_c = [[InlineKeyboardButton("✕ Cancel", callback_data="adm_cancel_course")]]
            if query.message.photo:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await query.message.reply_text(p_price, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_c))
            else:
                await query.edit_message_text(p_price, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_c))

    elif data == "adm_skip_link":
        new_course = context.user_data.get("new_course", {})
        new_course["access_link"] = ""
        context.user_data["new_course"] = new_course
        if new_course.get("image"):
            await send_admin_course_preview(query, context, new_course)
        else:
            context.user_data["course_step"] = "image"
            p_img = """🖼️ **ধাপ ৭/৭: কোর্সের ব্যানার বা ছবি পাঠান:**
━━━━━━━━━━━━━━━━━━━━
💡 ছবি ছাড়া প্রকাশ করতে চাইলে নিচের স্কিপ বাটনে চাপুন:"""
            k_img = [
                [InlineKeyboardButton("⏭️ Skip Image (ছবি ছাড়া প্রকাশ)", callback_data="adm_skip_img")],
                [InlineKeyboardButton("✕ Cancel", callback_data="adm_cancel_course")]
            ]
            if query.message.photo:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await query.message.reply_text(p_img, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_img))
            else:
                await query.edit_message_text(p_img, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_img))

    elif data == "adm_skip_img":
        new_course = context.user_data.get("new_course", {})
        new_course["image"] = ""
        context.user_data["new_course"] = new_course
        await send_admin_course_preview(query, context, new_course)

    elif data == "adm_pub_course":
        new_course = context.user_data.get("new_course", {})
        if not new_course or not new_course.get("name"):
            await query.answer("❌ কোনো কোর্স তথ্য পাওয়া যায়নি!", show_alert=True)
            return

        course_id = f"COURSE-{int(datetime.now().timestamp())}"
        new_course["id"] = course_id
        if "category" not in new_course or not new_course["category"]:
            new_course["category"] = "HSC 28"
        if "subcategory" not in new_course or not new_course["subcategory"]:
            new_course["subcategory"] = "Academic"
        if "program" not in new_course:
            new_course["program"] = str(new_course["subcategory"]).lower()
        if "price" not in new_course:
            new_course["price"] = 0
        if "description" not in new_course or not new_course["description"]:
            new_course["description"] = "কোর্সের বিস্তারিত তথ্য শীঘ্রই যুক্ত করা হবে।"
        if "features" not in new_course:
            new_course["features"] = new_course["description"]
        if "instructor" not in new_course:
            new_course["instructor"] = "অভিজ্ঞ শিক্ষকবৃন্দ"
        if "access_link" not in new_course:
            new_course["access_link"] = ""

        db.add_course(course_id, new_course)
        fld = new_course.get("folder_path", "").strip()
        if fld:
            fld_segments = [s.strip() for s in fld.split(">") if s.strip()]
            for i in range(len(fld_segments)):
                parent = " > ".join(fld_segments[:i])
                name = fld_segments[i]
                db.add_sub_folder(parent, name)
        else:
            db.add_category(new_course.get("category", "General"))

        context.user_data["admin_add_course"] = False
        context.user_data.pop("new_course", None)
        context.user_data.pop("course_step", None)

        await query.answer("🎉 কোর্সটি সফলভাবে পাবলিশ করা হয়েছে!", show_alert=True)

        price_tag = f"{new_course['price']} ৳" if new_course['price'] > 0 else "বিনামূল্যে (Free) 🎁"
        done_text = f"""🎉 <b>কোর্স সফলভাবে লাইভ পাবলিশ হয়েছে!</b>
━━━━━━━━━━━━━━━━━━━━

📖 <b>কোর্স:</b> {html.escape(new_course['name'])}
💰 <b>মূল্য:</b> {price_tag}
📂 <b>ক্যাটাগরি:</b> {html.escape(new_course['category'])} | {html.escape(new_course['subcategory'])}
🔗 <b>এক্সেস লিংক:</b> {html.escape(new_course['access_link'] or 'দেওয়া হয়নি')}

💡 শিক্ষার্থীরা এখন সরাসরি বটের মেনু থেকে এই কোর্সটি দেখতে ও কিনতে পারবে।"""

        k_done = [
            [InlineKeyboardButton("📁 Open Folder", callback_data=f"adm_dir_{fld}" if fld else "adm_categories")],
            [InlineKeyboardButton("➕ Add Another Course", callback_data=f"adm_addcourse_dir_{fld}" if fld else "adm_add_course")],
            [InlineKeyboardButton("📂 All Categories", callback_data="adm_categories")]
        ]
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(done_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(k_done))
        else:
            await query.edit_message_text(done_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(k_done))

    elif data == "adm_chg_img":
        context.user_data["course_step"] = "image_edit"
        msg_img = "🖼️ **কোর্সের নতুন ব্যানার বা ছবি পাঠান:**\n\n(ছবি সরাতে 'remove' লিখে পাঠান)\n(আগের ছবিতে ফিরে যেতে /cancel লিখুন)"
        k_back = [[InlineKeyboardButton("« Back to Preview", callback_data="adm_back_prev")]]
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg_img, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_back))
        else:
            await query.edit_message_text(msg_img, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_back))

    elif data == "adm_chg_cat":
        await send_admin_category_selector(query, context, "📂 **ক্যাটাগরি পরিবর্তন করুন:**")

    elif data == "adm_chg_fields":
        k_edit = [
            [InlineKeyboardButton("✏️ Name / Title", callback_data="adm_edprev_name"), InlineKeyboardButton("💰 Price", callback_data="adm_edprev_price")],
            [InlineKeyboardButton("📝 Description", callback_data="adm_edprev_desc"), InlineKeyboardButton("🔗 Access Link", callback_data="adm_edprev_link")],
            [InlineKeyboardButton("« Back to Preview", callback_data="adm_back_prev")]
        ]
        msg_f = "✏️ **যে তথ্যটি সংশোধন করতে চান তা নির্বাচন করুন:**"
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg_f, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_edit))
        else:
            await query.edit_message_text(msg_f, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_edit))

    elif data.startswith("adm_edprev_"):
        field = data.replace("adm_edprev_", "")
        context.user_data["course_step"] = f"edit_field_{field}"
        field_labels = {
            "name": "কোর্সের নতুন নাম (Course Title)",
            "price": "নতুন মূল্য (Price in BDT, e.g. 400 or 0)",
            "desc": "নতুন বিস্তারিত বিবরণ (Description)",
            "link": "নতুন টেলিগ্রাম/ড্রাইভ লিংক (Access Link)"
        }
        lbl = field_labels.get(field, field)
        p_txt = f"✏️ **{lbl} লিখে পাঠান:**\n\n(ফিরে যেতে নিচের বাটনে চাপুন)"
        k_b = [[InlineKeyboardButton("« Back to Preview", callback_data="adm_back_prev")]]
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(p_txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_b))
        else:
            await query.edit_message_text(p_txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_b))

    elif data == "adm_back_prev":
        new_course = context.user_data.get("new_course", {})
        await send_admin_course_preview(query, context, new_course)

    elif data in ("adm_cancel_course", "adm_course_cancel"):
        origin = context.user_data.pop("course_origin_callback", "adm_main")
        context.user_data["admin_add_course"] = False
        context.user_data.pop("new_course", None)
        context.user_data.pop("course_step", None)
        await query.answer("✕ কোর্স wizard বাতিল করা হয়েছে।")
        
        if origin == "adm_main":
            class FakeCallbackQuery:
                def __init__(self, message, data, user):
                    self.message = message
                    self.data = data
                    self.from_user = user
                    self.id = "0"
                async def answer(self, text=None, show_alert=False): pass
                async def edit_message_text(self, text, *args, **kwargs):
                    return await self.message.reply_text(text, *args, **kwargs)
            fake_query = FakeCallbackQuery(query.message, "adm_main", query.from_user)
            class FakeUpdate:
                def __init__(self, message, callback_query):
                    self.message = message
                    self.callback_query = callback_query
                    self.effective_user = callback_query.from_user
                    self.effective_chat = message.chat
            await handle_admin_callback(FakeUpdate(query.message, fake_query), context)
        elif origin.startswith("adm_dir_"):
            dir_path = origin.replace("adm_dir_", "")
            await render_admin_folder_directory(query, context, dir_path)
        else:
            k_cmenu = [
                [InlineKeyboardButton("➕ Add New Course", callback_data="adm_add_course")],
                [InlineKeyboardButton("📋 All Courses & Delete", callback_data="adm_list_courses")],
                [InlineKeyboardButton("« Admin Menu", callback_data="adm_main")]
            ]
            if query.message.photo:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await query.message.reply_text("❖ **Course Management Menu:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cmenu))
            else:
                await query.edit_message_text("❖ **Course Management Menu:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cmenu))

    elif data == "adm_course_back":
        step = context.user_data.get("course_step")
        new_course = context.user_data.get("new_course", {})
        
        if step == "price":
            context.user_data["course_step"] = "name"
            fld = new_course.get("folder_path", "General")
            cat = new_course.get("category", "General")
            msg = f"""📁 **ডিরেক্টরি: `{fld}`**
━━━━━━━━━━━━━━━━━━━━

✨ **ধাপ ১/৪: কোর্সের নাম (Course Name) লিখে পাঠান:**
(যেমন: BH Biology Full Course | {cat})

💡 ক্যাটাগরি ও ফোল্ডার স্বয়ংক্রিয়ভাবে `{fld}` ডিরেক্টরিতে সেট করা থাকবে।"""
            k_cancel = [[InlineKeyboardButton("✕ Cancel", callback_data=f"adm_dir_{fld}")]]
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cancel))
            
        elif step == "description":
            context.user_data["course_step"] = "price"
            p_price = f"📖 কোর্সের নাম: **{new_course.get('name', '')}**\n\n💰 **ধাপ ২/৪: কোর্সের মূল্য লিখুন (Price in BDT):**\n\n(যেমন: 400 বা ফ্রি কোর্সের জন্য 0 লিখুন)"
            keyboard = [
                [InlineKeyboardButton("« Back", callback_data="adm_course_back"), InlineKeyboardButton("✕ Cancel", callback_data="adm_course_cancel")]
            ]
            await query.edit_message_text(p_price, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif step == "link":
            context.user_data["course_step"] = "description"
            price_val = new_course.get('price', 0)
            price_tag = f"৳{price_val}" if price_val > 0 else "বিনামূল্যে (Free) 🎁"
            p_desc = f"💰 মূল্য: **{price_tag}**\n\n📝 **ধাপ ৩/৪: কোর্সের বিস্তারিত বিবরণ (Description) লিখুন:**\n\n💡 শিক্ষক প্যানেল, সিলেবাস এবং কোর্সের বিস্তারিত ফিচার্স লিখুন (একাধিক লাইনে লিখতে পারেন):"
            keyboard = [
                [InlineKeyboardButton("« Back", callback_data="adm_course_back"), InlineKeyboardButton("✕ Cancel", callback_data="adm_course_cancel")]
            ]
            await query.edit_message_text(p_desc, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif step == "image":
            context.user_data["course_step"] = "link"
            fld = new_course.get("folder_path", "General")
            p_text = f"""📁 **ডিরেক্টরি:** `{fld}`
━━━━━━━━━━━━━━━━━━━━
🔗 **ধাপ ৪/৪: টেলিগ্রাম প্রাইভেট চ্যানেল বা ড্রাইভ এক্সেস লিংক পাঠান:**

💡 লিংক না থাকলে বা পরে দিতে চাইলে নিচের স্কিপ বাটনে চাপুন:"""
            keyboard = [
                [InlineKeyboardButton("« Back", callback_data="adm_course_back"), InlineKeyboardButton("⏭️ Skip Link", callback_data="adm_skip_link")],
                [InlineKeyboardButton("✕ Cancel", callback_data="adm_course_cancel")]
            ]
            await query.edit_message_text(p_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "adm_list_courses":
        courses = db.get_all_courses()
        if not courses:
            await query.edit_message_text("❌ No courses found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="adm_courses")]]))
            return

        keyboard = []
        for cid, c in list(courses.items())[:30]:
            price_tag = f" ({c['price']}৳)" if c.get('price', 0) > 0 else " (Free)"
            keyboard.append([
                InlineKeyboardButton(f"{c['name']}{price_tag}", callback_data=f"adm_edit_{cid}")
            ])
        keyboard.append([InlineKeyboardButton("« Course Management", callback_data="adm_courses")])
        msg_text = "📋 **All Courses List:**\n━━━━━━━━━━━━━━━━━━━━\nSelect any course to view details, edit or delete:"
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text(msg_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("adm_course_toggle_"):
        cid = data.replace("adm_course_toggle_", "")
        new_status = db.toggle_course_status(cid)
        if new_status:
            status_text = "Enabled" if new_status == "active" else "Disabled"
            await query.answer(f"✅ Course {status_text} successfully!", show_alert=True)
            await show_course_edit_dashboard(query, cid)
        else:
            await query.answer("❌ Error toggling status!", show_alert=True)

    elif data.startswith("adm_course_clone_"):
        cid = data.replace("adm_course_clone_", "")
        new_cid = db.clone_course(cid)
        if new_cid:
            await query.answer("✅ Course Cloned (Copied) successfully!", show_alert=True)
            await show_course_edit_dashboard(query, new_cid)
        else:
            await query.answer("❌ Error cloning course!", show_alert=True)

    elif data.startswith("adm_course_mup_"):
        cid = data.replace("adm_course_mup_", "")
        success = db.move_course_order(cid, "up")
        if success:
            await query.answer("⬆️ Moved Up successfully!")
            await show_course_edit_dashboard(query, cid, "⬆️ **Moved Course Up**\n")
        else:
            await query.answer("⚠️ Already at the top of insertion order!", show_alert=True)

    elif data.startswith("adm_course_mdown_"):
        cid = data.replace("adm_course_mdown_", "")
        success = db.move_course_order(cid, "down")
        if success:
            await query.answer("⬇️ Moved Down successfully!")
            await show_course_edit_dashboard(query, cid, "⬇️ **Moved Course Down**\n")
        else:
            await query.answer("⚠️ Already at the bottom of insertion order!", show_alert=True)

    elif data.startswith("adm_course_mleft_"):
        cid = data.replace("adm_course_mleft_", "")
        success = db.move_course_left(cid)
        if success:
            await query.answer("✅ Course moved left (up one level)!", show_alert=True)
            await show_course_edit_dashboard(query, cid)
        else:
            await query.answer("⚠️ Already in a root category, cannot move left!", show_alert=True)

    elif data.startswith("adm_course_mright_"):
        cid = data.replace("adm_course_mright_", "")
        course = db.get_course(cid)
        if not course:
            await query.answer("Course not found!", show_alert=True)
            return
        c_fld = str(course.get("folder_path", "")).strip().replace(" / ", " > ").replace("/", " > ")
        subfolders = db.get_sub_folders(c_fld)
        if not subfolders:
            await query.answer("⚠️ No subfolders in this directory to move into!", show_alert=True)
            return
            
        context.user_data["move_course_id"] = cid
        context.user_data["move_course_subfolders"] = subfolders
        
        keyboard = []
        for idx, sf in enumerate(subfolders):
            keyboard.append([InlineKeyboardButton(f"📁 {sf}", callback_data=f"adm_course_doright_{idx}")])
        keyboard.append([InlineKeyboardButton("« Cancel", callback_data=f"adm_edit_{cid}")])
        
        await query.edit_message_text(
            f"➡️ **Select a subfolder to move the course '{course['name']}' into:**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("adm_course_doright_"):
        idx = int(data.replace("adm_course_doright_", ""))
        cid = context.user_data.get("move_course_id")
        subfolders = context.user_data.get("move_course_subfolders", [])
        
        if not cid or idx >= len(subfolders):
            await query.answer("Session expired or invalid choice!", show_alert=True)
            return
            
        target_folder = subfolders[idx]
        success = db.move_course_right(cid, target_folder)
        
        context.user_data.pop("move_course_id", None)
        context.user_data.pop("move_course_subfolders", None)
        
        if success:
            await query.answer(f"✅ Course moved into '{target_folder}'!", show_alert=True)
            await show_course_edit_dashboard(query, cid)
        else:
            await query.answer("❌ Move failed!", show_alert=True)

    elif data.startswith("adm_edit_"):
        cid = data.replace("adm_edit_", "")
        await show_course_edit_dashboard(query, cid)

    elif data.startswith("adm_edmenu_"):
        cid = data.replace("adm_edmenu_", "")
        course = db.get_course(cid)
        if not course:
            await query.answer("Course not found!")
            return

        text = f"✏️ **Edit Course: '{course['name']}'**\n━━━━━━━━━━━━━━━━━━━━\nSelect property to edit:"
        keyboard = [
            [InlineKeyboardButton("✏️ Edit Title", callback_data=f"edprop_{cid}_name"), InlineKeyboardButton("💰 Edit Price", callback_data=f"edprop_{cid}_price")],
            [InlineKeyboardButton("📂 Edit Category", callback_data=f"edprop_{cid}_category"), InlineKeyboardButton("🎯 Edit Sub-Category", callback_data=f"edprop_{cid}_subcategory")],
            [InlineKeyboardButton("🖼️ Edit Banner", callback_data=f"edprop_{cid}_image"), InlineKeyboardButton("🔗 Edit Link", callback_data=f"edprop_{cid}_access_link")],
            [InlineKeyboardButton("👨‍🏫 Edit Teacher", callback_data=f"edprop_{cid}_instructor"), InlineKeyboardButton("📝 Edit Details", callback_data=f"edprop_{cid}_description")],
            [InlineKeyboardButton("« Back", callback_data=f"adm_edit_{cid}")]
        ]
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("edprop_"):
        parts = data.split("_")
        cid = parts[1]
        prop = parts[2]
        context.user_data["admin_edit_cid"] = cid
        context.user_data["admin_edit_field"] = prop

        prop_prompts = {
            "name": "📝 **Send new Course Title:**",
            "price": "💰 **Send new Course Price (e.g. 250 or 0):**",
            "category": "📂 **Send new Category (e.g. SSC / HSC 28 / HSC 27):**",
            "subcategory": "🎯 **Send new Sub-Category (e.g. Physics / Academic):**",
            "image": "🖼️ **Send new Banner Photo or Image Web URL:**\n(Send `remove` to remove banner)",
            "access_link": "🔗 **Send new Telegram Channel / Drive Access Link:**",
            "instructor": "👨‍🏫 **Send Teacher / Instructor Name:**",
            "description": "📝 **Send new Course Description & Features:**"
        }
        prompt = prop_prompts.get(prop, "Send new value:")
        prompt += "\n\n(Type /cancel to abort)"

        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(prompt, parse_mode="Markdown")
        else:
            await query.edit_message_text(prompt, parse_mode="Markdown")

    # ==================== E-BOOK MANAGEMENT ====================
    elif data == "adm_ebooks":
        await render_admin_ebook_folder_directory(query, context, "")

    elif data.startswith("adm_ebdir_"):
        path = data.replace("adm_ebdir_", "")
        await render_admin_ebook_folder_directory(query, context, path)

    elif data == "adm_ebfld_addfolder":
        active_dir = context.user_data.get("active_eb_dir", "")
        context.user_data["admin_add_eb_subcat"] = True
        context.user_data["admin_eb_subcat_target_parent"] = active_dir
        parent_display = active_dir if active_dir else "Root (মূল ক্যাটাগরি)"
        
        msg = f"""📁 **ই-বুক ফোল্ডার অবস্থান:** `{parent_display}`
━━━━━━━━━━━━━━━━━━━━
➕ **নতুন ক্যাটাগরি বা ফোল্ডারের নাম লিখে পাঠান:**
(যেমন: HSC 26, Medical, Physics, Formula Sheet ইত্যাদি)

(বাতিল করতে /cancel লিখুন)"""
        k_c = [[InlineKeyboardButton("« Cancel", callback_data=f"adm_ebdir_{active_dir}")]]
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_c))
        else:
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_c))

    elif data == "adm_ebfld_addebook" or data.startswith("adm_addebook_dir_") or data == "adm_add_ebook":
        if data.startswith("adm_addebook_dir_"):
            active_dir = data.replace("adm_addebook_dir_", "")
        else:
            active_dir = context.user_data.get("active_eb_dir", "")

        segments = [s.strip() for s in active_dir.split(">") if s.strip()]
        cat = segments[0] if segments else "General"
        sub = segments[-1] if len(segments) > 1 else "General"

        context.user_data["active_eb_dir"] = active_dir
        context.user_data["admin_add_ebook"] = True
        context.user_data["ebook_step"] = "name"
        context.user_data["new_ebook"] = {
            "category": cat,
            "subcategory": sub,
            "folder_path": active_dir
        }

        path_disp = active_dir if active_dir else "General"
        msg = f"""📁 **ই-বুক ডিরেক্টরি: `{path_disp}`**
━━━━━━━━━━━━━━━━━━━━

✨ **ধাপ ১/৪: ই-বুকের নাম (E-Book Title) লিখে পাঠান:**
(যেমন: 📘 HSC Physics Formula Sheet | {cat})

💡 ক্যাটাগরি ও ফোল্ডার স্বয়ংক্রিয়ভাবে `{path_disp}` ডিরেক্টরিতে সেট করা থাকবে।"""
        k_cancel = [[InlineKeyboardButton("✕ Cancel", callback_data=f"adm_ebdir_{active_dir}")]]
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cancel))
        else:
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cancel))

    elif data.startswith("adm_ebfld_togglestatus_"):
        active_dir = context.user_data.get("active_eb_dir", "")
        if not active_dir:
            await query.answer("Cannot toggle root directory status!", show_alert=True)
            return
        new_st = db.toggle_ebook_category_status(active_dir)
        st_word = "🟢 Active (ON)" if new_st else "🔴 Inactive (OFF - Hidden)"
        await query.answer(f"E-Book Category status changed to {st_word}!", show_alert=True)
        await render_admin_ebook_folder_directory(query, context, active_dir)
        return

    elif data == "adm_ebfld_delfolder":
        active_dir = context.user_data.get("active_eb_dir", "")
        if not active_dir:
            await query.answer("Cannot delete root directory!", show_alert=True)
            return

        segments = [s.strip() for s in active_dir.split(">") if s.strip()]
        folder_to_delete = segments[-1]
        parent_path = " > ".join(segments[:-1]) if len(segments) > 1 else ""

        db.delete_ebook_sub_folder(parent_path, folder_to_delete)
        await query.answer(f"✅ '{folder_to_delete}' deleted!", show_alert=True)
        await render_admin_ebook_folder_directory(query, context, parent_path)

    elif data.startswith("adm_ebfld_moveup_") or data.startswith("adm_ebfld_movedown_"):
        direction = "up" if "moveup" in data else "down"
        folder_name = data.replace("adm_ebfld_moveup_", "").replace("adm_ebfld_movedown_", "")
        active_dir = context.user_data.get("active_eb_dir", "")
        segments = [s.strip() for s in active_dir.split(">") if s.strip()]
        parent_path = " > ".join(segments[:-1]) if len(segments) > 1 else ""
        success = db.move_ebook_folder_order(parent_path, folder_name, direction)
        if success:
            await query.answer(f"✅ Moved {direction}!", show_alert=True)
        else:
            await query.answer(f"Cannot move {direction}!", show_alert=True)
        await render_admin_ebook_folder_directory(query, context, active_dir)

    elif data.startswith("adm_ebfld_mleft_"):
        active_dir = context.user_data.get("active_eb_dir", "")
        segments = [s.strip() for s in active_dir.split(">") if s.strip()]
        if len(segments) <= 1:
            await query.answer("⚠️ Already a root category, cannot move left!", show_alert=True)
            return
        current_name = segments[-1]
        parent_path = " > ".join(segments[:-1])

        success = db.move_ebook_folder_left(parent_path, current_name)
        if success:
            parent_segments = parent_path.split(" > ")
            parent_parent = " > ".join(parent_segments[:-1]) if len(parent_segments) > 1 else ""
            new_active_dir = f"{parent_parent} > {current_name}" if parent_parent else current_name
            await query.answer("✅ Moved left successfully!", show_alert=True)
            await render_admin_ebook_folder_directory(query, context, new_active_dir)
        else:
            await query.answer("❌ Move left failed!", show_alert=True)

    elif data.startswith("adm_ebfld_mright_"):
        active_dir = context.user_data.get("active_eb_dir", "")
        segments = [s.strip() for s in active_dir.split(">") if s.strip()]
        if not segments:
            await query.answer("Cannot move!", show_alert=True)
            return
        current_name = segments[-1]
        parent_path = " > ".join(segments[:-1]) if len(segments) > 1 else ""

        siblings = [s for s in db.get_ebook_sub_folders(parent_path) if s != current_name]
        if not siblings:
            await query.answer("⚠️ No sibling folders to move into!", show_alert=True)
            return

        context.user_data["move_eb_folder_name"] = current_name
        context.user_data["move_eb_folder_parent"] = parent_path
        context.user_data["move_eb_folder_siblings"] = siblings

        keyboard = []
        for idx, s in enumerate(siblings):
            keyboard.append([InlineKeyboardButton(f"📁 {s}", callback_data=f"adm_ebfld_doright_{idx}")])
        keyboard.append([InlineKeyboardButton("« Cancel", callback_data=f"adm_ebdir_{active_dir}")])

        await query.edit_message_text(
            f"➡️ **Select a sibling folder to move '{current_name}' into:**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("adm_ebfld_doright_"):
        idx = int(data.replace("adm_ebfld_doright_", ""))
        current_name = context.user_data.get("move_eb_folder_name")
        parent_path = context.user_data.get("move_eb_folder_parent")
        siblings = context.user_data.get("move_eb_folder_siblings", [])

        if not current_name or idx >= len(siblings):
            await query.answer("Session expired or invalid choice!", show_alert=True)
            return

        sibling_name = siblings[idx]
        success = db.move_ebook_folder_right(parent_path, current_name, sibling_name)

        context.user_data.pop("move_eb_folder_name", None)
        context.user_data.pop("move_eb_folder_parent", None)
        context.user_data.pop("move_eb_folder_siblings", None)

        if success:
            await query.answer(f"✅ Moved '{current_name}' into '{sibling_name}'!", show_alert=True)
            await render_admin_ebook_folder_directory(query, context, parent_path)
        else:
            await query.answer("❌ Move failed!", show_alert=True)

    elif data.startswith("adm_ebfld_moveup") or data.startswith("adm_ebfld_movedown"):
        direction = "up" if "moveup" in data else "down"
        subfolders = db.get_ebook_sub_folders("")
        if not subfolders:
            await query.answer("No categories to move!", show_alert=True)
            return
        parent_path = ""
        folder_name = subfolders[0] if subfolders else ""
        if not folder_name:
            await query.answer("No category selected!", show_alert=True)
            return
        success = db.move_ebook_folder_order(parent_path, folder_name, direction)
        if success:
            await query.answer(f"✅ Moved {direction}!", show_alert=True)
        else:
            await query.answer(f"Cannot move {direction}!", show_alert=True)
        await render_admin_ebook_folder_directory(query, context, "")

    elif data.startswith("adm_ebfld_rename_"):
        folder_name = data.replace("adm_ebfld_rename_", "")
        active_dir = context.user_data.get("active_eb_dir", "")
        segments = [s.strip() for s in active_dir.split(">") if s.strip()]
        parent_path = " > ".join(segments[:-1]) if len(segments) > 1 else ""
        context.user_data["rename_eb_folder_old"] = folder_name
        context.user_data["rename_eb_folder_parent"] = parent_path
        context.user_data["awaiting_eb_folder_rename"] = True
        await query.edit_message_text(
            f"✏️ **'{folder_name}' ফোল্ডারের নতুন নাম লিখে পাঠান:**\n\n(নতুন নাম লিখুন অথবা নিচের বাটনে চাপুন)",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data=f"adm_ebdir_{active_dir}")]])
        )

    elif data.startswith("adm_setebcat_"):
        cat_selected = data.replace("adm_setebcat_", "")
        context.user_data["admin_add_ebook"] = True
        context.user_data["ebook_step"] = "name"
        context.user_data["new_ebook"] = {"category": cat_selected, "folder_path": cat_selected}

        msg = f"""📁 **ক্যাটাগরি: `{cat_selected}`**
━━━━━━━━━━━━━━━━━━━━

✨ **ধাপ ১/৪: ই-বুকের নাম (E-Book Title) লিখে পাঠান:**
(যেমন: 📘 HSC Physics Formula Sheet | {cat_selected})

💡 ক্যাটাগরি স্বয়ংক্রিয়ভাবে `{cat_selected}` সেট করা থাকবে।"""
        k_cancel = [[InlineKeyboardButton("✕ Cancel", callback_data=f"adm_ebdir_{cat_selected}")]]
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cancel))
        else:
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cancel))

    elif data == "adm_cancel_ebook":
        active_dir = context.user_data.get("active_eb_dir", "")
        context.user_data["admin_add_ebook"] = False
        context.user_data.pop("new_ebook", None)
        context.user_data.pop("ebook_step", None)
        await render_admin_ebook_folder_directory(query, context, active_dir)

    elif data == "adm_ebook_back":
        step = context.user_data.get("ebook_step")
        new_ebook = context.user_data.get("new_ebook", {})
        active_dir = new_ebook.get("folder_path") or new_ebook.get("category", "General")

        if step == "price":
            context.user_data["ebook_step"] = "name"
            msg = f"""📁 **ডিরেক্টরি: `{active_dir}`**
━━━━━━━━━━━━━━━━━━━━

✨ **ধাপ ১/৪: ই-বুকের নাম (E-Book Title) লিখে পাঠান:**
(যেমন: 📘 HSC Physics Formula Sheet | {active_dir})

💡 ক্যাটাগরি স্বয়ংক্রিয়ভাবে `{active_dir}` সেট করা থাকবে।"""
            k_cancel = [[InlineKeyboardButton("✕ Cancel", callback_data="adm_cancel_ebook")]]
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cancel))

        elif step == "description":
            context.user_data["ebook_step"] = "price"
            p_price = f"📖 ই-বুকের নাম: **{new_ebook.get('name', '')}**\n\n💰 **ধাপ ২/৪: ই-বুকের মূল্য লিখুন (Price in BDT):**\n\n(যেমন: 50 বা ফ্রি ই-বুকের জন্য 0 লিখুন)"
            keyboard = [
                [InlineKeyboardButton("« Back", callback_data="adm_ebook_back"), InlineKeyboardButton("✕ Cancel", callback_data="adm_cancel_ebook")]
            ]
            await query.edit_message_text(p_price, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

        elif step == "file_or_link":
            context.user_data["ebook_step"] = "description"
            price_val = new_ebook.get('price', 0)
            price_tag = f"৳{price_val}" if price_val > 0 else "বিনামূল্যে (Free) 🎁"
            p_desc = f"💰 মূল্য: **{price_tag}**\n\n📝 **ধাপ ৩/৪: ই-বুকের বিস্তারিত বিবরণ (Description) লিখুন:**\n\n💡 ই-বুকের বিষয়বস্তু বা বৈশিষ্ট্য লিখুন (অথবা স্কিপ করতে নিচের বাটনে চাপুন):"
            keyboard = [
                [InlineKeyboardButton("« Back", callback_data="adm_ebook_back"), InlineKeyboardButton("⏭️ Skip Description", callback_data="adm_skipeb_desc")],
                [InlineKeyboardButton("✕ Cancel", callback_data="adm_cancel_ebook")]
            ]
            await query.edit_message_text(p_desc, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "adm_skipeb_desc":
        new_ebook = context.user_data.get("new_ebook", {})
        new_ebook["description"] = "প্রিমিয়াম ই-বুক ও স্টাডি মেটেরিয়াল।"
        context.user_data["new_ebook"] = new_ebook
        active_dir = new_ebook.get("folder_path") or new_ebook.get("category", "General")

        if new_ebook.get("file_id"):
            await send_admin_ebook_preview(query, context, new_ebook)
        else:
            context.user_data["ebook_step"] = "file_or_link"
            p_text = f"""📁 **ডিরেক্টরি:** `{active_dir}`
━━━━━━━━━━━━━━━━━━━━
📄 **ধাপ ৪/৪: PDF ফাইলটি সরাসরি আপলোড করুন অথবা ডাউনলোড লিংক পাঠান:**

💡 ফাইল পাঠানোর নিয়ম:
• সরাসরি এই চ্যাটে Telegram **PDF Document** ফাইল সেন্ড করুন (সরাসরি ডাউনলোডের জন্য)।
• অথবা Google Drive / Web ডাউনলোড লিংক লিখে পাঠান।"""
            k_file = [
                [InlineKeyboardButton("« Back", callback_data="adm_ebook_back"), InlineKeyboardButton("⏭️ Skip File (পরে যোগ করবেন)", callback_data="adm_skipeb_file")],
                [InlineKeyboardButton("✕ Cancel", callback_data="adm_cancel_ebook")]
            ]
            await query.edit_message_text(p_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_file))

    elif data == "adm_skipeb_file":
        new_ebook = context.user_data.get("new_ebook", {})
        await send_admin_ebook_preview(query, context, new_ebook)

    elif data == "adm_publisheb":
        new_ebook = context.user_data.get("new_ebook", {})
        if not new_ebook.get("name"):
            await query.answer("⚠️ ই-বুকের তথ্য অসম্পূর্ণ!", show_alert=True)
            return

        ebook_id = f"EB-{int(datetime.now().timestamp())}"
        new_ebook["id"] = ebook_id
        active_fld = context.user_data.get("active_eb_dir", "")
        if "folder_path" not in new_ebook or not new_ebook["folder_path"]:
            new_ebook["folder_path"] = active_fld
        if "category" not in new_ebook or not new_ebook["category"]:
            segs = active_fld.split(" > ")
            new_ebook["category"] = segs[0] if segs else "General"
        if "price" not in new_ebook:
            new_ebook["price"] = 0
        if "description" not in new_ebook:
            new_ebook["description"] = "প্রিমিয়াম ই-বুক ও স্টাডি মেটেরিয়াল।"

        db.add_ebook(ebook_id, new_ebook)

        context.user_data["admin_add_ebook"] = False
        context.user_data.pop("new_ebook", None)
        context.user_data.pop("ebook_step", None)

        msg_success = f"""✅ **ই-বুক সফলভাবে প্রকাশিত হয়েছে!**
━━━━━━━━━━━━━━━━━━━━

📖 **নাম:** {new_ebook['name']}
📂 **ডিরেক্টরি:** `{new_ebook.get('folder_path') or new_ebook.get('category')}`
💰 **মূল্য:** {f"৳{new_ebook['price']}" if new_ebook['price'] > 0 else "Free 🎁"}"""

        keyboard = [
            [InlineKeyboardButton(f"📁 Open '{new_ebook.get('folder_path') or 'Folder'}'", callback_data=f"adm_ebdir_{new_ebook.get('folder_path', '')}")],
            [InlineKeyboardButton("➕ Add Another E-Book", callback_data=f"adm_addebook_dir_{new_ebook.get('folder_path', '')}")],
            [InlineKeyboardButton("« E-Book Management", callback_data="adm_ebooks")]
        ]
        await query.edit_message_text(msg_success, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("adm_vieweb_"):
        eid = data.replace("adm_vieweb_", "")
        await show_ebook_edit_dashboard(query, eid)

    elif data.startswith("adm_eb_toggle_"):
        eid = data.replace("adm_eb_toggle_", "")
        eb = db.get_ebook(eid)
        if eb:
            new_status = "inactive" if eb.get("status") != "inactive" else "active"
            db.update_ebook(eid, {"status": new_status})
            tag = "🔴 Disabled" if new_status == "inactive" else "🟢 Enabled"
            await query.answer(f"E-Book {tag}!", show_alert=True)
            await show_ebook_edit_dashboard(query, eid)

    elif data.startswith("adm_ebmove_mleft_"):
        eid = data.replace("adm_ebmove_mleft_", "")
        success = db.move_ebook_left(eid)
        if success:
            await query.answer("✅ Moved E-Book to parent folder!", show_alert=True)
            await show_ebook_edit_dashboard(query, eid)
        else:
            await query.answer("⚠️ Already at root folder, cannot move left!", show_alert=True)

    elif data.startswith("adm_ebmove_mright_"):
        eid = data.replace("adm_ebmove_mright_", "")
        eb = db.get_ebook(eid)
        if not eb:
            await query.answer("E-Book not found!", show_alert=True)
            return

        c_fld = str(eb.get("folder_path", "")).strip().replace(" / ", " > ").replace("/", " > ")
        subfolders = db.get_ebook_sub_folders(c_fld)
        if not subfolders:
            await query.answer("⚠️ No sub-folders available in current directory to move into!", show_alert=True)
            return

        context.user_data["move_eb_target_id"] = eid
        context.user_data["move_eb_target_fld"] = c_fld
        context.user_data["move_eb_target_subs"] = subfolders

        keyboard = []
        for idx, sf in enumerate(subfolders):
            keyboard.append([InlineKeyboardButton(f"📁 {sf}", callback_data=f"adm_ebmove_dosub_{idx}")])
        keyboard.append([InlineKeyboardButton("« Cancel", callback_data=f"adm_vieweb_{eid}")])

        await query.edit_message_text(
            f"➡️ **Select a sub-folder to move '{eb.get('name')}' into:**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("adm_ebmove_dosub_"):
        idx = int(data.replace("adm_ebmove_dosub_", ""))
        eid = context.user_data.get("move_eb_target_id")
        subfolders = context.user_data.get("move_eb_target_subs", [])

        if not eid or idx >= len(subfolders):
            await query.answer("Session expired!", show_alert=True)
            return

        target_sub = subfolders[idx]
        success = db.move_ebook_right(eid, target_sub)

        context.user_data.pop("move_eb_target_id", None)
        context.user_data.pop("move_eb_target_fld", None)
        context.user_data.pop("move_eb_target_subs", None)

        if success:
            await query.answer(f"✅ Moved into '{target_sub}'!", show_alert=True)
            await show_ebook_edit_dashboard(query, eid)
        else:
            await query.answer("❌ Move failed!", show_alert=True)

    elif data.startswith("adm_deleb_"):
        eid = data.replace("adm_deleb_", "")
        eb = db.get_ebook(eid)
        if eb:
            fld = eb.get("folder_path", "")
            db.delete_ebook(eid)
            await query.answer(f"✅ '{eb.get('name', 'E-Book')}' deleted successfully!", show_alert=True)
            await render_admin_ebook_folder_directory(query, context, fld)

    elif data.startswith("adm_editeb_"):
        eid = data.replace("adm_editeb_", "")
        eb = db.get_ebook(eid)
        if not eb:
            await query.answer("E-Book not found!")
            return

        text = f"✏️ **Edit E-Book: '{eb.get('name')}'**\n━━━━━━━━━━━━━━━━━━━━\nSelect property to edit:"
        keyboard = [
            [InlineKeyboardButton("✏️ Edit Title", callback_data=f"edebprop_{eid}_name"), InlineKeyboardButton("💰 Edit Price", callback_data=f"edebprop_{eid}_price")],
            [InlineKeyboardButton("📂 Edit Category", callback_data=f"edebprop_{eid}_category"), InlineKeyboardButton("📁 Edit File / Drive Link", callback_data=f"edebprop_{eid}_access_link")],
            [InlineKeyboardButton("📝 Edit Description", callback_data=f"edebprop_{eid}_description")],
            [InlineKeyboardButton("« Back", callback_data=f"adm_vieweb_{eid}")]
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("edebprop_"):
        parts = data.split("_")
        eid = parts[1]
        prop = parts[2]
        context.user_data["admin_edit_ebid"] = eid
        context.user_data["admin_edit_ebook_field"] = prop

        prompts = {
            "name": "Send new E-Book Title / Name:",
            "price": "Send new Price in BDT (Send `0` for Free):",
            "category": "Send new Category (e.g. HSC 28, SSC, Admission, General):",
            "access_link": "Upload new PDF Document file OR send Google Drive / Web link:",
            "description": "Send new Description text:"
        }
        await query.edit_message_text(
            f"✏️ **{prompts.get(prop, 'Send new value:')}**\n\n💡 Type /cancel to abort."
        )

    elif data == "adm_pending_orders" or data.startswith("adm_porders_"):
        page_num = int(data.replace("adm_porders_", "")) if data.startswith("adm_porders_") else 1
        pending, total_pages = db.get_paginated_pending_orders(page=page_num, per_page=6)
        if not pending:
            await query.edit_message_text(
                "✅ No pending orders currently!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Admin Menu", callback_data="adm_main")]])
            )
            return

        keyboard = []
        for o in pending:
            student_name = o.get('full_name') or o.get('username') or 'Student'
            keyboard.append([
                InlineKeyboardButton(
                    f"{format_order_id_display(o['order_id'])} — {student_name[:15]} — {o['amount']}৳ ({o.get('payment_method', '')})",
                    callback_data=f"ordinfo_{o['order_id']}"
                )
            ])
        nav_row = []
        if page_num > 1:
            nav_row.append(InlineKeyboardButton("◀️", callback_data=f"adm_porders_{page_num-1}"))
        else:
            nav_row.append(InlineKeyboardButton("⏹️", callback_data="adm_ignore"))

        nav_row.append(InlineKeyboardButton("🔍 Search", callback_data="adm_order_search"))

        if page_num < total_pages:
            nav_row.append(InlineKeyboardButton("▶️", callback_data=f"adm_porders_{page_num+1}"))
        else:
            nav_row.append(InlineKeyboardButton("⏹️", callback_data="adm_ignore"))

        keyboard.append(nav_row)
        keyboard.append([InlineKeyboardButton("« Admin Menu", callback_data="adm_main")])

        await query.edit_message_text(
            f"⏳ **Pending Orders (Page {page_num}/{total_pages}):**\nSelect an order to review & verify:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("ordinfo_"):
        order_id = data.replace("ordinfo_", "")
        order = db.get_order(order_id)
        if not order:
            await query.answer("Order not found!")
            return

        keyboard = [
[
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{order_id}"),
                InlineKeyboardButton("✕ Reject", callback_data=f"reject_{order_id}")
            ],
            [InlineKeyboardButton("« Pending Orders", callback_data="adm_pending_orders")]
        ]

        await query.edit_message_text(
            f"""📦 **Order Verification Details**
━━━━━━━━━━━━━━━━━━━━

🆔 **Order ID:** `{format_order_id_display(order_id)}`
👤 **Student:** {order.get('full_name', 'N/A')} (@{order.get('username', 'N/A')})
🆔 **User ID:** `{order.get('user_id')}`
📖 **Course:** {order.get('course_name', 'N/A')}
💰 **Amount:** {order['amount']} ৳ ({order.get('payment_method', '')})
🔑 **TrxID:** `{order.get('trxid', 'N/A')}`
🏷️ **Coupon:** `{order.get('coupon_code') or 'None'}`
📅 **Date:** {order.get('date', 'N/A')[:19]}
📌 **Status:** {order.get('status', '').upper()}""",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("approve_"):
        order_id = data.replace("approve_", "")
        order = db.get_order(order_id)
        if order and order.get("status") != "approved":
            db.update_order(order_id, {"status": "approved"})

            checkout_type = order.get("checkout_type", "single")
            access_links = []
            
            if checkout_type == "ebook":
                eb_id = order.get("course_id")
                db.add_ebook_purchase(order["user_id"], eb_id)
                eb_obj = db.get_ebook(eb_id)
                if eb_obj and eb_obj.get("access_link"):
                    access_links.append(f"➥ **{eb_obj['name']}:** {eb_obj['access_link']}")
            else:
                target_courses = order.get("courses", [])
                if not target_courses and order.get("course_id"):
                    target_courses = [order["course_id"]]

                for cid in target_courses:
                    db.add_purchase(order["user_id"], cid)
                    c_obj = db.get_course(cid)
                    if c_obj and c_obj.get("access_link"):
                        db.store_user_access_link(order["user_id"], cid, c_obj["access_link"])
                        access_links.append(f"➥ **{c_obj['name']}:** {c_obj['access_link']}")

            reward_res = db.trigger_referral_reward_for_order(order)
            if reward_res:
                reward_uid, r_amount, c_code = reward_res
                try:
                    await context.bot.send_message(
                        reward_uid,
                        f"""🎉 <b>Referral Reward Earned!</b>
━━━━━━━━━━━━━━━━━━━━

🏷️ <b>Coupon Code:</b> <code>{c_code}</code>
📦 <b>Order ID:</b> <code>{format_order_id_display(order_id)}</code>
💰 <b>Reward Earned:</b> <code>+৳{r_amount} BDT</code>

✨ <i>The reward has been added to your account balance. Check your profile to view your balance.</i>""",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"রেফারেল রিওয়ার্ড নোটিফিকেশন পাঠাতে সমস্যা: {e}")

            access_text = "\n".join(access_links) if access_links else "আমাদের বটে '✦ আমার কোর্সসমূহ' মেনুতে আপনার কোর্স লিংক যুক্ত করা হয়েছে।"
            
            # Dynamically select appropriate delivery template
            is_free = False
            try:
                if int(order.get("amount", 0)) == 0:
                    is_free = True
            except Exception:
                pass
                
            has_link = len(access_links) > 0
            
            if is_free:
                if has_link:
                    template_key = "delivery_free_with_link"
                    default_delivery = "🎉 **Your Course Access is Confirmed!**\n\n**Course:** {course_name}\n\n👇 Click the button below to join your course:\n{access_text}"
                else:
                    template_key = "delivery_free_no_link"
                    default_delivery = "🎉 **Your Course Access is Confirmed!**\n\n**Course:** {course_name}\n\n⚠️ No direct link is currently available. Please contact admin."
            else:
                if has_link:
                    template_key = "delivery_paid_with_link"
                    default_delivery = "🎉 **Your Course Purchase was Successful!**\n\n**Course:** {course_name}\n**Order ID:** `{order_id}`\n\n✅ **Payment completed successfully.**\n\n👇 Click the button below to join your course:\n{access_text}"
                else:
                    template_key = "delivery_paid_no_link"
                    default_delivery = "🎉 **Your Course Purchase was Successful!**\n\n**Course:** {course_name}\n**Order ID:** `{order_id}`\n\n✅ **Payment completed successfully.**\n\n⚠️ No direct link is attached to this course. Please contact support."

            delivery_template = db.get_setting(template_key) or db.get_setting("delivery_message") or default_delivery
            cleaned_delivery, custom_kb = parse_inline_buttons(delivery_template)

            c_name = str(order.get('course_name') or 'N/A')
            o_id = format_order_id_display(order_id)
            amt = str(order.get('amount', 0))
            acc = str(access_text) if access_text else ""

            user_notify = cleaned_delivery
            for pat in ["${courseName}", "{courseName}", "${course_name}", "{course_name}"]:
                user_notify = user_notify.replace(pat, c_name)
            for pat in ["${orderId}", "{orderId}", "${order_id}", "{order_id}"]:
                user_notify = user_notify.replace(pat, o_id)
            for pat in ["${amount}", "{amount}"]:
                user_notify = user_notify.replace(pat, amt)
            for pat in ["${access_text}", "{access_text}", "${accessText}", "{accessText}"]:
                user_notify = user_notify.replace(pat, acc)

            keyboard = []
            if checkout_type == "ebook":
                eb_id = order.get("course_id")
                eb_obj = db.get_ebook(eb_id)
                if eb_obj:
                    if eb_obj.get("file_id"):
                        keyboard.append([InlineKeyboardButton("📥 Download (PDF)", callback_data=f"ebdl_{eb_id}")])
                    elif eb_obj.get("access_link"):
                        keyboard.append([InlineKeyboardButton("📥 Download E-Book", url=eb_obj["access_link"])])
                keyboard.append([InlineKeyboardButton("📚 My E-Books", callback_data="my_ebooks_nav")])
            else:
                if access_links:
                    for cid in target_courses:
                        c_obj = db.get_course(cid)
                        if c_obj and c_obj.get("access_link"):
                            dyn_link = await get_dynamic_access_link(context.bot, c_obj["access_link"], order["user_id"])
                            if dyn_link:
                                keyboard.append([InlineKeyboardButton(f"{c_obj['name']}", url=dyn_link)])
                keyboard.append([InlineKeyboardButton("🎓 My Courses", callback_data="my_courses_nav")])

            if custom_kb:
                keyboard.extend(custom_kb)

            try:
                await context.bot.send_message(
                    order["user_id"],
                    user_notify,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception as e:
                logger.error(f"ইউজারকে নোটিফিকেশন পাঠাতে সমস্যা: {e}")

            # Paid Referral Bonus Conversion
            try:
                order_amount = int(order.get("amount", 0))
            except Exception:
                order_amount = 0

            if order_amount > 0:
                conv_success, referrer_id, reward_amt, new_bal = db.process_paid_referral_conversion(
                    buyer_id=order["user_id"],
                    order_id=order_id,
                    course_name=order.get("course_name", "Paid Course")
                )
                if conv_success and referrer_id:
                    try:
                        await context.bot.send_message(
                            referrer_id,
                            f"""🎁 <b>Referral Bonus Earned!</b>
━━━━━━━━━━━━━━━━━━━━
👤 A student joined through your referral link has successfully purchased a course!

📦 <b>Order ID:</b> <code>{format_order_id_display(order_id)}</code>
💰 <b>Bonus Earned:</b> <code>+৳{reward_amt} BDT</code>
💳 <b>Current Balance:</b> <code>৳{new_bal} BDT</code>

💡 <i>You can use your balance to purchase any course or e-book directly from the bot.</i>""",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Failed to send referral bonus reward notice: {e}")

            await query.edit_message_text(
                f"✅ **Order `{order_id}` approved successfully and access links delivered to user!**",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Pending Orders", callback_data="adm_pending_orders")]])
            )

    elif data.startswith("reject_"):
        order_id = data.replace("reject_", "")
        order = db.get_order(order_id)
        if order and order.get("status") != "rejected":
            db.update_order(order_id, {"status": "rejected"})
            try:
                await context.bot.send_message(
                    order["user_id"],
                    f"""❌ **দুঃখিত! আপনার অর্ডারটি বাতিল করা হয়েছে।**
━━━━━━━━━━━━━━━━━━━━

📦 **Order ID:** `{format_order_id_display(order_id)}`
📖 **কোর্স:** {order.get('course_name', 'N/A')}
🔑 **TrxID:** `{order.get('trxid', 'N/A')}`

⚠️ **সম্ভাব্য কারণ:** ভুল TrxID বা অপ্রতুল পেমেন্ট।
সহায়তার জন্য সরাসরি এডমিনের সাথে যোগাযোগ করুন: @{SUPPORT_USERNAME}""",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"ইউজারকে রিজেক্ট মেসেজ পাঠাতে সমস্যা: {e}")

            await query.edit_message_text(
                f"❌ **Order `{order_id}` rejected!**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Pending Orders", callback_data="adm_pending_orders")]])
            )

    # ==================== COUPON & REFERRAL REWARD WIZARD ====================
    elif data == "adm_coupons":
        coupons = db.get_all_coupons()
        active_count = sum(1 for c in coupons.values() if c.get("status", "active") == "active")
        inactive_count = len(coupons) - active_count
        r_stats = db.get_referral_global_stats()
        ref_st_icon = "🟢 ON" if r_stats["is_enabled"] else "🔴 OFF"

        msg = f"""<blockquote>🎟️ <b>[ Coupon & Referral Rewards Hub ]</b></blockquote>

<blockquote>🏷️ <b>Coupons Overview:</b>
• <b>Total Coupons:</b> <code>{len(coupons)}</code>
• <b>Active:</b> <code>{active_count}</code> | <b>Inactive:</b> <code>{inactive_count}</code></blockquote>

<blockquote>🎁 <b>Refer & Earn Program:</b>
• <b>System Status:</b> <b>{ref_st_icon}</b>
• <b>Bonus Per Referral:</b> <code>৳{r_stats['bonus_amount']} BDT</code>
• <b>Total Referrers:</b> <code>{r_stats['total_referrers']}</code>
• <b>Successful (Paid) Refs:</b> <code>{r_stats['total_converted']}</code>
• <b>Total User Balance:</b> <code>৳{r_stats['total_balance']} BDT</code></blockquote>

<blockquote>👇 <b>Select an option below:</b></blockquote>"""

        keyboard = [
            [InlineKeyboardButton("👥 Refer & Earn Manager", callback_data="adm_referrals")],
            [InlineKeyboardButton("➕ Create New Coupon", callback_data="adm_add_coupon_start")],
            [InlineKeyboardButton("📋 All Coupons List", callback_data="adm_list_coupons")],
            [InlineKeyboardButton(f"🔄 Refer System: {ref_st_icon}", callback_data="adm_tog_referral")],
            [InlineKeyboardButton("« Admin Menu", callback_data="adm_main")]
        ]
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "adm_referrals":
        await render_admin_referrals_hub(query, context)

    elif data == "adm_tog_referral":
        new_st = db.toggle_referral_system()
        st_word = "🟢 ON (Active)" if new_st else "🔴 OFF (Disabled)"
        await query.answer(f"Referral system changed to {st_word}!", show_alert=True)
        await render_admin_referrals_hub(query, context)

    elif data.startswith("adm_reflist_"):
        page_num = int(data.replace("adm_reflist_", ""))
        await render_admin_referrers_list(query, context, page=page_num)

    elif data.startswith("adm_refuview_"):
        target_uid = int(data.replace("adm_refuview_", ""))
        await render_admin_referral_user_view(query, context, target_uid)

    elif data == "adm_ref_search":
        context.user_data["admin_user_step"] = "search_referral_user"
        await query.edit_message_text(
            """<blockquote>🔍 <b>Search Referral User / রেফারেল ইউজার খুঁজুন</b></blockquote>

<blockquote>📝 <b>নির্দেশনা:</b>
ইউজারের <b>Telegram User ID</b>, <b>Name</b> অথবা <b>Username</b> লিখে পাঠান।
(যেমন: <code>7610279126</code> বা <code>username</code>)</blockquote>

<blockquote>❌ বাতিল করতে <code>/cancel</code> লিখুন।</blockquote>""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data="adm_referrals")]])
        )

    elif data == "adm_ref_set_bonus":
        context.user_data["admin_user_step"] = "set_referral_bonus"
        cur_bonus = db.get_referral_reward_amount()
        await query.edit_message_text(
            f"""<blockquote>💵 <b>Set Referral Bonus Amount</b></blockquote>

<blockquote>💰 <b>বর্তমান বোনাস:</b> <code>৳{cur_bonus} BDT</code> per paid referral</blockquote>

<blockquote>👇 <b>প্রতি সফল পেইড রেফারের নতুন বোনাসের পরিমাণ লিখুন (যেমন: 50 বা 100):</b></blockquote>

<blockquote>❌ বাতিল করতে <code>/cancel</code> লিখুন।</blockquote>""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel", callback_data="adm_referrals")]])
        )

    elif data == "adm_add_coupon_start":
        context.user_data["admin_add_coupon_wizard"] = True
        context.user_data["coupon_wiz_step"] = "code"
        context.user_data["wiz_coupon"] = {}
        await query.edit_message_text(
            "🏷️ **Enter New Coupon Code**\n\n"
            "Send your coupon code below.\n"
            "👉 Example: `SPECIAL50` or `RATUL20`\n\n"
            "❌ To cancel, send `/cancel`.",
            parse_mode="Markdown"
        )

    elif data.startswith("cpnwiz_type_"):
        dtype = data.replace("cpnwiz_type_", "")
        context.user_data["wiz_coupon"]["discount_type"] = dtype
        context.user_data["coupon_wiz_step"] = "discount_value"

        prompt = "💰 **Enter Fixed Discount Amount (৳):**" if dtype == "fixed" else "📊 **Enter Discount Rate (%):**"
        await query.edit_message_text(prompt, parse_mode="Markdown")

    elif data.startswith("cpnwiz_scope_"):
        scope = data.replace("cpnwiz_scope_", "")
        if scope == "all":
            context.user_data["wiz_coupon"]["applicable_category"] = "All"
            context.user_data["wiz_coupon"]["applicable_course_id"] = None
            context.user_data["wiz_coupon"]["applicable_course_name"] = None
            context.user_data["coupon_wiz_step"] = "usage_limit"
            await query.edit_message_text("🔢 **Enter Maximum Usage Limit (e.g., 100):**", parse_mode="Markdown")

        elif scope == "cat":
            cats = db.get_categories()
            keyboard = []
            row = []
            for c in cats:
                row.append(InlineKeyboardButton(f"📁 {c}", callback_data=f"cpnwiz_cat_{c}"))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)
            keyboard.append([InlineKeyboardButton("« Cancel", callback_data="adm_coupons")])
            await query.edit_message_text("📂 **Select category:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

        elif scope == "course" or scope.startswith("course_pg_"):
            page_num = int(scope.replace("course_pg_", "")) if scope.startswith("course_pg_") else 1
            await render_coupon_course_select_page(query, page=page_num)

    elif data.startswith("cpnwiz_cat_"):
        cat_chosen = data.replace("cpnwiz_cat_", "")
        context.user_data["wiz_coupon"]["applicable_category"] = cat_chosen
        context.user_data["wiz_coupon"]["applicable_course_id"] = None
        context.user_data["wiz_coupon"]["applicable_course_name"] = None
        context.user_data["coupon_wiz_step"] = "usage_limit"
        await query.edit_message_text(f"✅ Category `{cat_chosen}` selected.\n\n🔢 **Enter Maximum Usage Limit (e.g., 100):**", parse_mode="Markdown")

    elif data.startswith("cpnwiz_course_"):
        cid_chosen = data.replace("cpnwiz_course_", "")
        c_obj = db.get_course(cid_chosen)
        c_name = c_obj.get("name", "Specific Course") if c_obj else "Specific Course"
        context.user_data["wiz_coupon"]["applicable_course_id"] = cid_chosen
        context.user_data["wiz_coupon"]["applicable_course_name"] = c_name
        context.user_data["wiz_coupon"]["applicable_category"] = "Specific"
        context.user_data["coupon_wiz_step"] = "usage_limit"
        await query.edit_message_text(f"✅ Course `{c_name}` selected.\n\n🔢 **Enter Maximum Usage Limit (e.g., 100):**", parse_mode="Markdown")

    elif data.startswith("cpnwiz_ref_"):
        choice = data.replace("cpnwiz_ref_", "")
        if choice == "yes":
            context.user_data["wiz_coupon"]["enable_referral_reward"] = True
            context.user_data["coupon_wiz_step"] = "reward_user_id"
            await query.edit_message_text("👤 **Enter Telegram User ID of recipient (e.g. 7610279126):**", parse_mode="Markdown")
        else:
            context.user_data["wiz_coupon"]["enable_referral_reward"] = False
            context.user_data["wiz_coupon"]["reward_user_id"] = None
            context.user_data["wiz_coupon"]["reward_type"] = "fixed"
            context.user_data["wiz_coupon"]["reward_amount"] = 0
            await finalize_coupon_creation(query, context, user_id)

    elif data.startswith("cpnwiz_reftype_"):
        rtype = data.replace("cpnwiz_reftype_", "")
        context.user_data["wiz_coupon"]["reward_type"] = rtype
        context.user_data["coupon_wiz_step"] = "reward_amount"
        r_uid = context.user_data["wiz_coupon"].get("reward_user_id", "")
        if rtype == "fixed":
            prompt = f"💰 **Enter Fixed Reward Amount in BDT for User `{r_uid}` (e.g., 30 or 50):**"
        else:
            prompt = f"📊 **Enter Reward Percentage (%) for User `{r_uid}` (e.g., 10 or 15):**"
        await query.edit_message_text(prompt, parse_mode="Markdown")

    elif data == "adm_list_coupons" or data.startswith("adm_list_coupons_"):
        page_num = int(data.replace("adm_list_coupons_", "")) if data.startswith("adm_list_coupons_") else 1
        await render_admin_coupon_list(query, page=page_num)

    elif data.startswith("adm_view_coupon_"):
        code = data.replace("adm_view_coupon_", "")
        c = db.get_coupon(code)
        if not c:
            await query.answer("❌ No coupon found!", show_alert=True)
            return

        status = c.get("status", "active")
        status_text = "🟢 Active " if status == "active" else "🔴 Inactive "
        dtype_s = "৳ (Fixed)" if c.get("discount_type") == "fixed" else "% (Percentage)"
        dval = c.get("discount_value", c.get("discount", 0))

        if c.get("applicable_course_name"):
            scope_desc = f"🎯 Specific Course: `{c['applicable_course_name']}`"
        elif c.get("applicable_category") and c["applicable_category"] not in ["All", "Specific"]:
            scope_desc = f"📂 Category: `{c['applicable_category']}`"
        else:
            scope_desc = "🌐 All Courses"

        if c.get("enable_referral_reward") and c.get("reward_user_id"):
            rtype = "৳ (Fixed)" if c.get("reward_type") == "fixed" else "% (Percentage)"
            ref_info = f"👤 User ID: `{c.get('reward_user_id')}`\n🎁 **Commission:** {c.get('reward_amount', 0)} {rtype}"
        else:
            ref_info = "Disabled"

        msg = f"""🏷️ **Coupon Code:** `{code}`
━━━━━━━━━━━━━━━━━━━━

📊 **Current Status:** {status_text}
💰 **Discount:** {dval} {dtype_s}
🎯 **Scope:** {scope_desc}
🛒 **Minimum Purchase:** {c.get('min_purchase', 0)} ৳
🔢 **Usage:** {c.get('used_count', 0)} / {c.get('usage_limit', c.get('uses', 100))} Times
🤝 **Referral Reward:** {ref_info}"""

        toggle_btn_text = "🔴 Deactivate Coupon" if status == "active" else "🟢 Activate Coupon"
        keyboard = [
            [InlineKeyboardButton(toggle_btn_text, callback_data=f"adm_toggle_coupon_{code}")],
            [InlineKeyboardButton("🗑️ Delete Coupon", callback_data=f"adm_delcoupon_{code}")],
            [InlineKeyboardButton("« Back to List", callback_data="adm_list_coupons")]
        ]
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("adm_toggle_coupon_"):
        code = data.replace("adm_toggle_coupon_", "")
        c = db.get_coupon(code)
        if c:
            curr_status = c.get("status", "active")
            new_status = "inactive" if curr_status == "active" else "active"
            db.set_coupon_status(code, new_status)
            status_msg = "Deactivated" if new_status == "inactive" else "Activated"
            await query.answer(f"✅ Coupon '{code}' successfully {status_msg}!", show_alert=True)
            # Refresh coupon view
            c = db.get_coupon(code)
            status = c.get("status", "active")
            status_text = "🟢 Active " if status == "active" else "🔴 Inactive "
            dtype_s = "৳ (Fixed)" if c.get("discount_type") == "fixed" else "% (Percentage)"
            dval = c.get("discount_value", c.get("discount", 0))

            if c.get("applicable_course_name"):
                scope_desc = f"🎯 Specific Course: `{c['applicable_course_name']}`"
            elif c.get("applicable_category") and c["applicable_category"] not in ["All", "Specific"]:
                scope_desc = f"📂 Category: `{c['applicable_category']}`"
            else:
                scope_desc = "🌐 All Courses (Global)"

            if c.get("enable_referral_reward") and c.get("reward_user_id"):
                rtype = "৳ (Fixed)" if c.get("reward_type") == "fixed" else "% (Percentage)"
                ref_info = f"👤 User ID: `{c.get('reward_user_id')}`\n🎁 **Commission:** {c.get('reward_amount', 0)} {rtype}"
            else:
                ref_info = "Disabled"

            msg = f"""🏷️ **Coupon Code:** `{code}`
━━━━━━━━━━━━━━━━━━━━

📊 **Current Status:** {status_text}
💰 **Discount:** {dval} {dtype_s}
🎯 **Scope:** {scope_desc}
🛒 **Minimum Purchase:** {c.get('min_purchase', 0)} ৳
🔢 **Usage:** {c.get('used_count', 0)} / {c.get('usage_limit', c.get('uses', 100))} Times
🤝 **Referral Reward:** {ref_info}"""

            toggle_btn_text = "🔴 Deactivate Coupon" if status == "active" else "🟢 Activate Coupon"
            keyboard = [
                [InlineKeyboardButton(toggle_btn_text, callback_data=f"adm_toggle_coupon_{code}")],
                [InlineKeyboardButton("🗑️ Delete Coupon", callback_data=f"adm_delcoupon_{code}")],
                [InlineKeyboardButton("« Back to List", callback_data="adm_list_coupons")]
            ]
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("adm_delcoupon_"):
        code = data.replace("adm_delcoupon_", "")
        db.delete_coupon(code)
        await query.answer(f"✅ Coupon '{code}' deleted successfully!", show_alert=True)
        coupons = db.get_all_coupons()
        keyboard = []
        for c_code, c_val in coupons.items():
            status_icon = "🟢" if c_val.get("status", "active") == "active" else "🔴"
            dtype_s = "৳" if c_val.get("discount_type") == "fixed" else "%"
            keyboard.append([
                InlineKeyboardButton(f"{status_icon} {c_code} ({c_val.get('discount_value', 0)}{dtype_s})", callback_data=f"adm_view_coupon_{c_code}")
            ])
        keyboard.append([InlineKeyboardButton("➕ Create Coupon", callback_data="adm_add_coupon_start")])
        keyboard.append([InlineKeyboardButton("« Back", callback_data="adm_coupons")])
        await query.edit_message_text("📋 **All Coupons List:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "adm_stats":
        stats = db.get_stats()
        await query.edit_message_text(
            f"""📊 **Business Statistics Overview**
━━━━━━━━━━━━━━━━━━━━

👥 **Total Active Students:** {stats['total_users']}
📚 **Total Ready Courses:** {stats['total_courses']}
📦 **Total Orders:** {stats['total_orders']}
⏳ **Pending Orders:** {stats['pending_orders']}
💸 **Pending Withdrawals:** {stats.get('pending_withdrawals', 0)}
💵 **Total Gross Sales:** {stats['total_revenue']} ৳
💳 **Total Withdrawals Paid:** {stats.get('total_withdrawn', 0)} ৳""",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Admin Menu", callback_data="adm_main")]])
        )

    elif data == "adm_all_orders" or data.startswith("adm_aorders_"):
        page_num = int(data.replace("adm_aorders_", "")) if data.startswith("adm_aorders_") else 1
        orders, total_pages = db.get_paginated_all_orders(page=page_num, per_page=6)

        if not orders:
            await query.edit_message_text("❌ No order records found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Back", callback_data="adm_main")]]))
            return

        keyboard = []
        for o in orders:
            status_symbol = "⏳" if o['status'] == 'pending' else "✅" if o['status'] == 'approved' else "❌"
            student_name = o.get('full_name') or o.get('username') or 'Student'
            keyboard.append([
                InlineKeyboardButton(
                    f"{status_symbol} {format_order_id_display(o['order_id'])} — {student_name[:15]} — {o['amount']}৳",
                    callback_data=f"ordinfo_{o['order_id']}"
                )
            ])
        nav_row = []
        if page_num > 1:
            nav_row.append(InlineKeyboardButton("◀️", callback_data=f"adm_aorders_{page_num-1}"))
        else:
            nav_row.append(InlineKeyboardButton("⏹️", callback_data="adm_ignore"))

        nav_row.append(InlineKeyboardButton("🔍 Search", callback_data="adm_order_search"))

        if page_num < total_pages:
            nav_row.append(InlineKeyboardButton("▶️", callback_data=f"adm_aorders_{page_num+1}"))
        else:
            nav_row.append(InlineKeyboardButton("⏹️", callback_data="adm_ignore"))

        keyboard.append(nav_row)
        keyboard.append([InlineKeyboardButton("« Back", callback_data="adm_main")])

        await query.edit_message_text(f"📋 **All Orders (Page {page_num}/{total_pages}):**", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== PAYMENT SETTINGS & USER MGMT HANDLERS ====================

async def handle_admin_payment_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip() if update.message.text else ""
    user_id = update.effective_user.id
    step = context.user_data.get("admin_pay_step")

    if text == "/cancel":
        context.user_data.pop("admin_pay_step", None)
        context.user_data.pop("admin_pay_target_key", None)
        context.user_data.pop("new_pay_method", None)
        back_kb = [[InlineKeyboardButton("⚙ Payment Settings", callback_data="adm_payments")]]
        await update.message.reply_text("✕ Action aborted.", reply_markup=InlineKeyboardMarkup(back_kb))
        return

    if step == "edit_num":
        pkey = context.user_data.get("admin_pay_target_key")
        db.update_payment_method(pkey, {"number": text})
        context.user_data.pop("admin_pay_step", None)
        context.user_data.pop("admin_pay_target_key", None)
        await update.message.reply_text(
            f"✅ **{pkey.title()} number updated to `{text}`!**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙ Payment Settings", callback_data="adm_payments")]])
        )

    elif step == "edit_ins":
        pkey = context.user_data.get("admin_pay_target_key")
        db.update_payment_method(pkey, {"instruction": text})
        context.user_data.pop("admin_pay_step", None)
        context.user_data.pop("admin_pay_target_key", None)
        await update.message.reply_text(
            f"✅ **{pkey.title()} instruction updated to `{text}`!**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙ Payment Settings", callback_data="adm_payments")]])
        )

    elif step == "edit_note":
        db.set_payment_note(text)
        context.user_data.pop("admin_pay_step", None)
        await update.message.reply_text(
            "✅ **Payment notes & disclaimer updated successfully!**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙ Payment Settings", callback_data="adm_payments")]])
        )

    elif step == "add_name":
        context.user_data["new_pay_method"]["name"] = text
        context.user_data["admin_pay_step"] = "add_num"
        await update.message.reply_text(f"📱 **Enter number for {text}:**\n(e.g. 01XXXXXXXXX)")

    elif step == "add_num":
        context.user_data["new_pay_method"]["number"] = text
        context.user_data["admin_pay_step"] = "add_ins"
        await update.message.reply_text("⚙ **Enter instruction/type (e.g. Personal / Send Money / Merchant):**")

    elif step == "add_ins":
        m_name = context.user_data["new_pay_method"]["name"]
        m_num = context.user_data["new_pay_method"]["number"]
        m_ins = text

        db.add_payment_method(name=m_name, number=m_num, instruction=m_ins)
        context.user_data.pop("admin_pay_step", None)
        context.user_data.pop("new_pay_method", None)

        await update.message.reply_text(
            f"""🎉 **New Payment Method Added!**
━━━━━━━━━━━━━━━━━━━━

💳 **Name:** {m_name}
📱 **Number:** `{m_num}`
⚙ **Type:** {m_ins}""",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙ Payment Settings", callback_data="adm_payments")]])
        )


async def handle_admin_user_mgmt_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip() if update.message.text else ""
    user_id = update.effective_user.id
    step = context.user_data.get("admin_user_step")

    if text == "/cancel":
        context.user_data.pop("admin_user_step", None)
        context.user_data.pop("admin_dm_target_uid", None)
        uid = step.replace("adjust_bal_", "") if (step and step.startswith("adjust_bal_")) else None
        back_kb = []
        if uid:
            back_kb.append([InlineKeyboardButton("👤 View User Profile", callback_data=f"adm_uview_{uid}")])
        back_kb.append([InlineKeyboardButton("« User Management Menu", callback_data="adm_users")])
        await update.message.reply_text("✕ Aborted.", reply_markup=InlineKeyboardMarkup(back_kb))
        return

    if step == "set_referral_bonus":
        context.user_data.pop("admin_user_step", None)
        try:
            val = int(text)
            if val < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ অনুগ্রহ করে সঠিক ধনাত্মক পূর্ণসংখ্যা লিখুন (যেমন: 50 বা 100):")
            return

        db.set_referral_reward_amount(val)
        keyboard = [
            [InlineKeyboardButton("👥 Refer & Earn Hub", callback_data="adm_referrals")],
            [InlineKeyboardButton("« Admin Menu", callback_data="adm_main")]
        ]
        await update.message.reply_text(
            f"✅ **Success!**\n🎁 প্রতি সফল পেইড রেফারেলের বোনাস **৳{val} BDT** নির্ধারণ করা হয়েছে।",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if step == "search_referral_user":
        context.user_data.pop("admin_user_step", None)
        results = db.search_referral_users(text)
        if not results:
            await update.message.reply_text(
                f"❌ No student found matching '{text}'!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Refer & Earn Hub", callback_data="adm_referrals")]])
            )
            return

        keyboard = []
        for u in results[:10]:
            u_name = u.get("full_name") or u.get("username") or str(u.get("user_id"))
            ref_c = u.get("referral_count", 0)
            bal = u.get("balance", 0)
            keyboard.append([
                InlineKeyboardButton(f"👤 {u_name[:16]} — 👥 {ref_c} | 💰 ৳{bal}", callback_data=f"adm_refuview_{u['user_id']}")
            ])
        keyboard.append([InlineKeyboardButton("« Refer & Earn Hub", callback_data="adm_referrals")])

        await update.message.reply_text(
            f"🔍 <b>Referral Search Results for '{text}' ({len(results)} found):</b>\nClick to view referral details:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if step and step.startswith("adjust_bal_"):
        uid = int(step.replace("adjust_bal_", ""))
        context.user_data.pop("admin_user_step", None)
        try:
            val = int(text)
        except ValueError:
            await update.message.reply_text("⚠️ Please enter a valid integer (e.g., 500):")
            return

        db.update_balance(uid, val)
        keyboard = [
            [InlineKeyboardButton("👤 View User Profile", callback_data=f"adm_uview_{uid}")],
            [InlineKeyboardButton("« User Menu", callback_data="adm_users")]
        ]
        await update.message.reply_text(
            f"✅ **Success!**\n👤 User `{uid}` balance has been updated to **{val} ৳**!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if step == "search":
        context.user_data.pop("admin_user_step", None)
        results = db.search_users(text)
        if not results:
            await update.message.reply_text(
                f"❌ No student found matching '{text}'!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« User Menu", callback_data="adm_users")]])
            )
            return

        keyboard = []
        for u in results[:10]:
            u_name = u.get("full_name") or u.get("username") or str(u.get("user_id"))
            courses_c = len(u.get("purchased_courses", []))
            is_adm_badge = " 👑" if db.is_admin(u['user_id']) else ""
            keyboard.append([
                InlineKeyboardButton(f"{u_name[:20]}{is_adm_badge} (📚 {courses_c})", callback_data=f"adm_uview_{u['user_id']}")
            ])
        keyboard.append([InlineKeyboardButton("« User Menu", callback_data="adm_users")])

        await update.message.reply_text(
            f"🔍 **Search Results for '{text}' ({len(results)} found):**\nClick to view profile:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif step == "add_admin":
        context.user_data.pop("admin_user_step", None)
        target_uid = None

        clean_text = text.replace("@", "").strip()
        if clean_text.isdigit():
            target_uid = int(clean_text)
        else:
            for uid_val, u_data in db.users.items():
                if u_data.get("username", "").lower() == clean_text.lower():
                    target_uid = int(uid_val)
                    break

        if not target_uid:
            keyboard = [
                [InlineKeyboardButton("➕ Try Again", callback_data="adm_add_admin")],
                [InlineKeyboardButton("« Admin List", callback_data="adm_admin_list")]
            ]
            await update.message.reply_text(
                f"❌ User not found! Ensure that `{text}` has started the bot or provide a valid User ID.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        added = db.add_admin(target_uid, added_by=user_id)
        if added:
            try:
                await context.bot.send_message(
                    target_uid,
                    "🎉 **Congratulations!**\nYou have been added as an admin to **StudyMart**.\n👉 Use /admin command to open the admin panel.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

            u_info = db.get_user(target_uid)
            u_name = (u_info.get("full_name") if u_info else "") or f"User {target_uid}"
            keyboard = [
                [InlineKeyboardButton("🔐 Configure Permissions (পারমিশন সেট করুন)", callback_data=f"adm_perm_{target_uid}")],
                [InlineKeyboardButton("👑 View Admin List", callback_data="adm_admin_list"), InlineKeyboardButton("👤 User Profile", callback_data=f"adm_uview_{target_uid}")],
                [InlineKeyboardButton("« User Management", callback_data="adm_users")]
            ]
            await update.message.reply_text(
                f"✅ **Success!**\n👑 **{u_name}** (`{target_uid}`) has been successfully appointed as an admin!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            keyboard = [
                [InlineKeyboardButton("👑 View Admin List", callback_data="adm_admin_list")],
                [InlineKeyboardButton("« User Management", callback_data="adm_users")]
            ]
            await update.message.reply_text(
                f"ℹ️ User `{target_uid}` is already an admin!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif step == "send_dm":
        target_uid = context.user_data.get("admin_dm_target_uid")
        context.user_data.pop("admin_user_step", None)
        context.user_data.pop("admin_dm_target_uid", None)

        try:
            await context.bot.send_message(
                target_uid,
                f"➥ **StudyMart Admin Support Message:**\n━━━━━━━━━━━━━━━━━━━━\n\n{text}",
                parse_mode="Markdown"
            )
            await update.message.reply_text(
                f"✅ **Message delivered to User `{target_uid}` inbox!**",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👤 User Profile", callback_data=f"adm_uview_{target_uid}")]])
            )
        except Exception as e:
            await update.message.reply_text(
                f"❌ Could not send message: {e}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👤 User Profile", callback_data=f"adm_uview_{target_uid}")]])
            )


# ==================== BROADCAST SYSTEM HANDLERS ====================

def render_broadcast_target_selector_keyboard(context, page: int = 1, search_results: list = None):
    selected = context.user_data.setdefault("bc_recipients", [])
    
    # Get user list
    if search_results is not None:
        users_list = search_results
        total_pages = 1
    else:
        # Get users from database
        all_users = list(db.users.values())
        all_users.sort(key=lambda u: u.get("joined_date", ""), reverse=True)
        
        per_page = 5
        total = len(all_users)
        total_pages = max(1, (total + per_page - 1) // per_page)
        
        if page < 1: page = 1
        if page > total_pages: page = total_pages
        
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        users_list = all_users[start_idx:end_idx]
        
    keyboard = []
    
    # Render user buttons
    for u in users_list:
        uid = int(u["user_id"])
        name = u.get("full_name") or "User"
        uname = u.get("username")
        uname_disp = f" (@{uname})" if uname else ""
        
        is_selected = uid in selected
        icon = "✅" if is_selected else "➕"
        
        keyboard.append([
            InlineKeyboardButton(f"{icon} {name}{uname_disp} [{uid}]", callback_data=f"bc_sel_tgl_{uid}_{page}")
        ])
        
    # Render pagination if not showing search results
    if search_results is None:
        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("« Prev", callback_data=f"bc_sel_pg_{page-1}"))
        nav_row.append(InlineKeyboardButton(f"Page {page}/{total_pages}", callback_data="bc_sel_pg_noop"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("Next »", callback_data=f"bc_sel_pg_{page+1}"))
        keyboard.append(nav_row)
    else:
        # Back to browsing list button for search results
        keyboard.append([InlineKeyboardButton("« Back to User List", callback_data="bc_sel_pg_1")])
        
    # Actions row
    keyboard.append([
        InlineKeyboardButton("🔍 Search User", callback_data="bc_sel_search"),
        InlineKeyboardButton(f"✅ Confirm ({len(selected)} Selected)", callback_data="bc_sel_done")
    ])
    keyboard.append([InlineKeyboardButton("✕ Cancel Broadcast", callback_data="adm_bc_cancel")])
    
    return InlineKeyboardMarkup(keyboard)


def get_broadcast_target_selector_text(context, search_query: str = ""):
    selected = context.user_data.get("bc_recipients", [])
    
    # Build list of selected users
    selected_details = []
    for uid in selected[:15]:
        user_info = db.get_user(uid)
        if user_info:
            name = user_info.get("full_name") or "User"
            uname = user_info.get("username")
            uname_disp = f" (@{uname})" if uname else ""
            selected_details.append(f"• 👤 {name}{uname_disp} (`{uid}`)")
        else:
            selected_details.append(f"• 👤 Unknown User (`{uid}`)")
            
    if len(selected) > 15:
        selected_details.append(f"• ... and {len(selected) - 15} more.")
        
    selected_text = "\n".join(selected_details) if selected_details else "_(None selected)_"
    
    search_header = f"🔍 **Search Results for:** `{search_query}`\n\n" if search_query else ""
    
    text = f"""◈ **Select Target Users:**
━━━━━━━━━━━━━━━━━━━━
{search_header}📌 **Selected Recipients ({len(selected)}):**
{selected_text}

👇 **How to select target users:**
1. Click on a user button below to toggle selection.
2. Click **Search User** to find specific users by Name/Username/ID.
3. Paste/type multiple User IDs in the chat directly to add them instantly.

💡 Click **Confirm** when you are done to proceed."""
    return text

async def handle_broadcast_input_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = msg.text.strip() if msg and msg.text else ""
    user_id = update.effective_user.id
    step = context.user_data.get("admin_broadcasting_step")

    if text == "/cancel" or text == "❌ Cancel" or text.lower() == "cancel":
        context.user_data.pop("admin_broadcasting_mode", None)
        context.user_data.pop("admin_broadcasting_step", None)
        context.user_data.pop("bc_payload", None)
        context.user_data.pop("bc_recipients", None)
        await update.message.reply_text("✕ Broadcast cancelled.", reply_markup=main_menu_keyboard(user_id))
        return

    if step == "msg_content":
        payload = {}
        if msg.photo:
            payload["type"] = "photo"
            payload["file_id"] = msg.photo[-1].file_id
            payload["caption"] = msg.caption or ""
        elif msg.video:
            payload["type"] = "video"
            payload["file_id"] = msg.video.file_id
            payload["caption"] = msg.caption or ""
        elif msg.document:
            payload["type"] = "document"
            payload["file_id"] = msg.document.file_id
            payload["caption"] = msg.caption or ""
        elif text:
            payload["type"] = "text"
            payload["text"] = text

        if not payload:
            await update.message.reply_text("⚠️ Unsupported message type! Please send Text, Photo, Video, or Document (or click Cancel):")
            return

        context.user_data["bc_payload"] = payload
        context.user_data["admin_broadcasting_step"] = "btn_input"

        await update.message.reply_text(
            """➥ **Do you want to attach a URL button to this message?**
━━━━━━━━━━━━━━━━━━━━

Format: `Button Title | https://link`
Example: `Join Live Class | https://t.me/example`

💡 Type `skip` if you don't need any button.""",
            reply_markup=ReplyKeyboardMarkup([["❌ Cancel"]], resize_keyboard=True)
        )

    elif step == "btn_input":
        payload = context.user_data.get("bc_payload", {})
        if text.lower() != "skip":
            if "|" in text:
                parts = text.split("|", 1)
                btn_title = parts[0].strip()
                btn_url = parts[1].strip()
                if btn_title and btn_url.startswith("http"):
                    payload["btn_text"] = btn_title
                    payload["btn_url"] = btn_url
                else:
                    await update.message.reply_text("⚠️ Invalid URL! The link must start with http:// or https://. Send again or type `skip`:")
                    return
            else:
                await update.message.reply_text("⚠️ Invalid format! Please send: `Button Title | https://link` or type `skip`:")
                return

        context.user_data["bc_payload"] = payload
        context.user_data["admin_broadcasting_step"] = "target_select"

        keyboard = [
            [
                InlineKeyboardButton("👥 All Users", callback_data="adm_bc_target_all"),
                InlineKeyboardButton("◈ Selected Users", callback_data="adm_bc_target_sel")
            ],
            [InlineKeyboardButton("✕ Cancel", callback_data="adm_bc_cancel")]
        ]
        
        # Restore main menu keyboard at this selection step
        await update.message.reply_text(
            "📌 **Select Broadcast Target Audience:**",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif step == "fwd_content":
        if not msg:
            await update.message.reply_text("⚠️ Invalid message! Please forward a message:")
            return

        payload = {
            "type": "forward",
            "from_chat_id": msg.chat_id,
            "message_id": msg.message_id
        }
        context.user_data["bc_payload"] = payload
        context.user_data["admin_broadcasting_step"] = "target_select"

        keyboard = [
            [
                InlineKeyboardButton("👥 All Users", callback_data="adm_bc_target_all"),
                InlineKeyboardButton("◈ Selected Users", callback_data="adm_bc_target_sel")
            ],
            [InlineKeyboardButton("✕ Cancel", callback_data="adm_bc_cancel")]
        ]
        
        # Restore main menu keyboard at this selection step
        await update.message.reply_text(
            "📌 **Select Broadcast Target Audience:**",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif step == "search_users_for_bc":
        results = db.search_users(text)
        if not results:
            await update.message.reply_text(
                f"❌ No users found matching '{text}'! Try again or cancel search:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Cancel Search", callback_data="bc_sel_pg_1")]])
            )
            return
            
        context.user_data["bc_search_query"] = text
        reply_markup = render_broadcast_target_selector_keyboard(context, search_results=results)
        msg_text = get_broadcast_target_selector_text(context, search_query=text)
        await update.message.reply_text(
            msg_text,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        context.user_data["admin_broadcasting_step"] = "sel_uids"
        return

    elif step == "sel_uids":
        raw = text.replace(",", " ").split()
        recipients = context.user_data.setdefault("bc_recipients", [])
        added_count = 0
        for item in raw:
            if item.isdigit():
                uid = int(item)
                if uid not in recipients:
                    recipients.append(uid)
                    added_count += 1

        if added_count == 0:
            await update.message.reply_text("⚠️ No new/valid User IDs found! Send again or use the buttons below:")
            return

        context.user_data["bc_recipients"] = recipients
        
        page = 1
        search_query = context.user_data.get("bc_search_query", "")
        if search_query:
            results = db.search_users(search_query)
            await update.message.reply_text(
                get_broadcast_target_selector_text(context, search_query),
                parse_mode="Markdown",
                reply_markup=render_broadcast_target_selector_keyboard(context, search_results=results)
            )
        else:
            await update.message.reply_text(
                get_broadcast_target_selector_text(context),
                parse_mode="Markdown",
                reply_markup=render_broadcast_target_selector_keyboard(context, page=page)
            )
        return


async def show_broadcast_preview(target, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    payload = context.user_data.get("bc_payload", {})
    recipients = context.user_data.get("bc_recipients", [])
    target_chat_id = target.chat_id if hasattr(target, "chat_id") else user_id

    reply_markup = None
    if payload.get("btn_text") and payload.get("btn_url"):
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(payload["btn_text"], url=payload["btn_url"])]])

    try:
        ptype = payload.get("type")
        if ptype == "forward":
            await context.bot.copy_message(
                chat_id=target_chat_id,
                from_chat_id=payload["from_chat_id"],
                message_id=payload["message_id"]
            )
        elif ptype == "photo":
            await context.bot.send_photo(
                chat_id=target_chat_id,
                photo=payload["file_id"],
                caption=payload.get("caption", ""),
                reply_markup=reply_markup
            )
        elif ptype == "video":
            await context.bot.send_video(
                chat_id=target_chat_id,
                video=payload["file_id"],
                caption=payload.get("caption", ""),
                reply_markup=reply_markup
            )
        elif ptype == "document":
            await context.bot.send_document(
                chat_id=target_chat_id,
                document=payload["file_id"],
                caption=payload.get("caption", ""),
                reply_markup=reply_markup
            )
        else:
            await context.bot.send_message(
                chat_id=target_chat_id,
                text=payload.get("text", ""),
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Preview error: {e}")

    conf_msg = f"""⚡ **Broadcast Preview Complete!**
━━━━━━━━━━━━━━━━━━━━

👥 **Total Recipients:** `{len(recipients)}` users

Click Send Broadcast to dispatch now:"""

    keyboard = [
        [
            InlineKeyboardButton("🚀 Send Broadcast", callback_data="adm_bc_confirm_send"),
            InlineKeyboardButton("✕ Cancel", callback_data="adm_bc_cancel")
        ]
    ]

    # Restore main menu keyboard for the admin
    await context.bot.send_message(
        chat_id=target_chat_id,
        text="👇 **Confirm broadcast options below:**",
        reply_markup=main_menu_keyboard(user_id)
    )

    await context.bot.send_message(
        chat_id=target_chat_id,
        text=conf_msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def execute_broadcast(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    payload = context.user_data.get("bc_payload", {})
    recipients = context.user_data.get("bc_recipients", [])

    context.user_data.pop("admin_broadcasting_mode", None)
    context.user_data.pop("admin_broadcasting_step", None)
    context.user_data.pop("bc_payload", None)
    context.user_data.pop("bc_recipients", None)

    if not recipients:
        await query.edit_message_text("❌ No recipients found!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Admin Menu", callback_data="adm_main")]]))
        return

    progress = await query.edit_message_text(f"🚀 Sending broadcast to {len(recipients)} users...")

    sent = 0
    failed = 0
    reply_markup = None
    if payload.get("btn_text") and payload.get("btn_url"):
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(payload["btn_text"], url=payload["btn_url"])]])

    ptype = payload.get("type")

    for uid in recipients:
        try:
            if ptype == "forward":
                await context.bot.copy_message(
                    chat_id=uid,
                    from_chat_id=payload["from_chat_id"],
                    message_id=payload["message_id"]
                )
            elif ptype == "photo":
                await context.bot.send_photo(
                    chat_id=uid,
                    photo=payload["file_id"],
                    caption=payload.get("caption", ""),
                    reply_markup=reply_markup
                )
            elif ptype == "video":
                await context.bot.send_video(
                    chat_id=uid,
                    video=payload["file_id"],
                    caption=payload.get("caption", ""),
                    reply_markup=reply_markup
                )
            elif ptype == "document":
                await context.bot.send_document(
                    chat_id=uid,
                    document=payload["file_id"],
                    caption=payload.get("caption", ""),
                    reply_markup=reply_markup
                )
            else:
                await context.bot.send_message(
                    chat_id=uid,
                    text=payload.get("text", ""),
                    reply_markup=reply_markup
                )
            sent += 1
            await asyncio.sleep(0.04)
        except Exception:
            failed += 1

    await progress.edit_text(
        f"""✅ **Broadcast Complete!**
━━━━━━━━━━━━━━━━━━━━

📤 **Sent:** {sent}
❌ **Failed:** {failed}""",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Admin Menu", callback_data="adm_main")]])
    )


# ==================== ADMIN COUPON CREATION WIZARD ====================

async def handle_admin_coupon_creation_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    step = context.user_data.get("coupon_wiz_step")
    wiz = context.user_data.get("wiz_coupon", {})
    user_id = update.effective_user.id

    if text == "/cancel":
        context.user_data["admin_add_coupon_wizard"] = False
        context.user_data.pop("wiz_coupon", None)
        context.user_data.pop("coupon_wiz_step", None)
        back_kb = [[InlineKeyboardButton("🎟 Coupon Management", callback_data="adm_coupons")]]
        await update.message.reply_text("✕ Coupon creation aborted.", reply_markup=InlineKeyboardMarkup(back_kb))
        return

    if step == "code":
        wiz["code"] = text.upper()
        context.user_data["wiz_coupon"] = wiz
        context.user_data["coupon_wiz_step"] = "type_select"

        keyboard = [
            [
                InlineKeyboardButton("💵 Fixed Discount (BDT)", callback_data="cpnwiz_type_fixed"),
                InlineKeyboardButton("📊 Percentage Discount (%)", callback_data="cpnwiz_type_percentage")
            ]
        ]
        await update.message.reply_text(
            f"🏷️ **Coupon Code:** `{wiz['code']}`\n\n👇 **Select Discount Type:**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif step == "discount_value":
        try:
            wiz["discount_value"] = int(text)
        except ValueError:
            await update.message.reply_text("⚠️ Please enter a valid number:")
            return
        context.user_data["wiz_coupon"] = wiz
        context.user_data["coupon_wiz_step"] = "min_purchase"
        await update.message.reply_text("💰 **Minimum purchase amount required to use this coupon?**\n(Enter `0` if no minimum limit):", parse_mode="Markdown")

    elif step == "min_purchase":
        try:
            wiz["min_purchase"] = int(text)
        except ValueError:
            wiz["min_purchase"] = 0
        context.user_data["wiz_coupon"] = wiz
        context.user_data["coupon_wiz_step"] = "scope_select"

        keyboard = [
            [InlineKeyboardButton("🌐 All Courses", callback_data="cpnwiz_scope_all")],
            [InlineKeyboardButton("📂 By Category", callback_data="cpnwiz_scope_cat")],
            [InlineKeyboardButton("🎯 Specific Course", callback_data="cpnwiz_scope_course")]
        ]
        await update.message.reply_text(
            "🎯 **Coupon Scope:**\n━━━━━━━━━━━━━━━━━━━━\nThis coupon will be applicable to which course?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif step == "usage_limit":
        try:
            wiz["usage_limit"] = int(text)
        except ValueError:
            wiz["usage_limit"] = 100
        context.user_data["wiz_coupon"] = wiz
        context.user_data["coupon_wiz_step"] = "referral_select"

        keyboard = [
            [
                InlineKeyboardButton("✅ Yes (Enable Reward)", callback_data="cpnwiz_ref_yes"),
                InlineKeyboardButton("✕ No (No Reward)", callback_data="cpnwiz_ref_no")
            ]
        ]
        await update.message.reply_text(
            """🎁 **Want to add Referral/Affiliate Reward?**
━━━━━━━━━━━━━━━━━━━━
Will any specific partner/user get a commission/reward from the sales made using this coupon?""",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif step == "reward_user_id":
        try:
            wiz["reward_user_id"] = int(text)
        except ValueError:
            await update.message.reply_text("⚠️ Please enter a valid Telegram User ID (Numeric):")
            return
        context.user_data["wiz_coupon"] = wiz
        context.user_data["coupon_wiz_step"] = "reward_type_select"

        keyboard = [
            [
                InlineKeyboardButton("💵 Fixed Amount (BDT)", callback_data="cpnwiz_reftype_fixed"),
                InlineKeyboardButton("📊 Percentage (%)", callback_data="cpnwiz_reftype_percentage")
            ]
        ]
        await update.message.reply_text(
            f"👤 **User ID:** `{wiz['reward_user_id']}`\n\n👇 **Select Commission/Reward Type:**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif step == "reward_amount":
        try:
            wiz["reward_amount"] = int(text)
        except ValueError:
            wiz["reward_amount"] = 0
        context.user_data["wiz_coupon"] = wiz

        await finalize_coupon_creation(update.message, context, user_id)


async def finalize_coupon_creation(target, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    wiz = context.user_data.get("wiz_coupon", {})
    code = wiz.get("code", "COUPON")
    dtype = wiz.get("discount_type", "fixed")
    dval = wiz.get("discount_value", 0)
    min_p = wiz.get("min_purchase", 0)
    cat = wiz.get("applicable_category", "All")
    cid = wiz.get("applicable_course_id")
    cname = wiz.get("applicable_course_name")
    limit = wiz.get("usage_limit", 100)
    ref_on = wiz.get("enable_referral_reward", False)
    r_uid = wiz.get("reward_user_id")
    r_type = wiz.get("reward_type", "fixed")
    r_amt = wiz.get("reward_amount", 0)

    db.add_coupon(
        code=code,
        discount_type=dtype,
        discount_value=dval,
        min_purchase=min_p,
        applicable_category=cat,
        applicable_course_id=cid,
        applicable_course_name=cname,
        usage_limit=limit,
        enable_referral_reward=ref_on,
        reward_user_id=r_uid,
        reward_type=r_type,
        reward_amount=r_amt
    )

    context.user_data["admin_add_coupon_wizard"] = False
    context.user_data.pop("wiz_coupon", None)
    context.user_data.pop("coupon_wiz_step", None)

    if cid and cname:
        scope_str = f"🎯 Course: `{cname}`"
    elif cat and cat not in ["All", "Specific"]:
        scope_str = f"📂 Category: `{cat}`"
    else:
        scope_str = "🌐 All Courses (Global)"

    if ref_on and r_uid:
        rtype_s = "৳ (Fixed)" if r_type == "fixed" else "% (Percentage)"
        ref_info = f"\n🎁 **Referral Reward:** {r_amt} {rtype_s} ➡️ User ID: `{r_uid}`"
    else:
        ref_info = "\n🎁 **Referral Reward:** Disabled"

    dtype_str = f"{dval} ৳ (Fixed)" if dtype == "fixed" else f"{dval}% (Percentage)"

    msg = f"""🎉 **Coupon created successfully!**
━━━━━━━━━━━━━━━━━━━━

🏷️ **Coupon Code:** `{code}`
💰 **Discount:** {dtype_str}
🎯 **Scope:** {scope_str}
🛒 **Minimum Order:** {min_p} ৳
🔢 **Usage Limit:** {limit} times{ref_info}"""

    keyboard = [
        [InlineKeyboardButton("📋 All Coupons List", callback_data="adm_list_coupons")],
        [InlineKeyboardButton("➕ Create Another Coupon", callback_data="adm_add_coupon_start")],
        [InlineKeyboardButton("« Coupon Management", callback_data="adm_coupons")]
    ]

    if hasattr(target, "reply_text"):
        await target.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await target.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


# ==================== ADMIN COURSE CREATION FLOW ====================

async def show_course_edit_dashboard(query, course_id: str, notice: str = ""):
    course = db.get_course(course_id)
    if not course:
        await query.answer("❌ Course not found!", show_alert=True)
        return

    price_str = f"{course['price']} ৳" if course.get('price', 0) > 0 else "Free 🎁"
    has_img = "Yes ✅" if course.get("image") else "No ❌"
    status_tag = "🔴 Disabled (Hidden from Students)" if course.get("status") == "inactive" else "🟢 Enabled (Visible to Students)"
    toggle_label = "🟢 Enable Course" if course.get("status") == "inactive" else "🔴 Disable Course"

    msg = f"""{notice}📖 **Course Details & Management**
━━━━━━━━━━━━━━━━━━━━

📖 **Title:** {course['name']}
💰 **Price:** {price_str}
📂 **Category:** {course.get('category', 'N/A')}
🎯 **Sub-Category:** {course.get('subcategory', course.get('program', 'N/A'))}
👨‍🏫 **Instructor:** {course.get('instructor', 'N/A')}
🔗 **Access Link:** {course.get('access_link', 'N/A')}
🖼️ **Banner Photo:** {has_img}
✨ **Status:** {status_tag}

📝 **Description:**
{course.get('description', 'N/A')}

✨ **Features:**
{course.get('features', 'N/A')}

👇 **Select an option to manage this course:**"""

    keyboard = [
        [
            InlineKeyboardButton("✏️ Edit Details", callback_data=f"adm_edmenu_{course_id}"),
            InlineKeyboardButton(toggle_label, callback_data=f"adm_course_toggle_{course_id}")
        ],
        [
            InlineKeyboardButton("📋 Copy (Clone)", callback_data=f"adm_course_clone_{course_id}"),
            InlineKeyboardButton("✕ Delete", callback_data=f"adm_delcourse_{course_id}")
        ],
        [
            InlineKeyboardButton("⬅️", callback_data=f"adm_course_mleft_{course_id}"),
            InlineKeyboardButton("⬆️", callback_data=f"adm_course_mup_{course_id}"),
            InlineKeyboardButton("⬇️", callback_data=f"adm_course_mdown_{course_id}"),
            InlineKeyboardButton("➡️", callback_data=f"adm_course_mright_{course_id}")
        ],
        [InlineKeyboardButton("« Back to Folder", callback_data=f"adm_dir_{course.get('category', '')} > {course.get('subcategory', '')}")]
    ]

    image = course.get("image")
    if image:
        try:
            await query.message.delete()
        except Exception:
            pass
        try:
            await query.message.reply_photo(photo=image, caption=msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        if query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_admin_category_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    context.user_data["admin_add_category"] = False

    if text == "/cancel":
        back_kb = [[InlineKeyboardButton("📂 Categories Management", callback_data="adm_categories")]]
        await update.message.reply_text("✕ Aborted.", reply_markup=InlineKeyboardMarkup(back_kb))
        return

    added = db.add_category(text)
    if added:
        keyboard = [
            [InlineKeyboardButton(f"➕ Add Sub-Category to '{text}'", callback_data=f"adm_addsub_{text}")],
            [InlineKeyboardButton(f"📂 Manage '{text}'", callback_data=f"adm_catm_{text}")],
            [InlineKeyboardButton("« All Categories", callback_data="adm_categories")]
        ]
        await update.message.reply_text(
            f"✅ **New Category '{text}' created successfully!**\n\n💡 No automatic sub-categories were created. You can add sub-categories as needed:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            f"ℹ️ Category '{text}' already exists!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📂 Categories Management", callback_data="adm_categories")]])
        )


async def handle_admin_subcategory_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    parent = context.user_data.get("admin_subcat_target_parent") or context.user_data.get("admin_subcat_target_cat", "")
    context.user_data["admin_add_subcat"] = False
    context.user_data.pop("admin_subcat_target_parent", None)
    context.user_data.pop("admin_subcat_target_cat", None)

    if text == "/cancel":
        back_kb = [[InlineKeyboardButton(f"« Back to '{parent or 'All Categories'}'", callback_data=f"adm_dir_{parent}")] if parent else [InlineKeyboardButton("« All Categories", callback_data="adm_categories")]]
        await update.message.reply_text("✕ Aborted.", reply_markup=InlineKeyboardMarkup(back_kb))
        return

    added = db.add_sub_folder(parent, text)
    new_path = f"{parent} > {text}" if parent else text
    if added:
        keyboard = [
            [InlineKeyboardButton(f"📁 Open '{text}' Folder", callback_data=f"adm_dir_{new_path}")],
            [InlineKeyboardButton(f"➕ Add Course in '{text}'", callback_data=f"adm_addcourse_dir_{new_path}")],
            [InlineKeyboardButton(f"➕ Add Another Sub-Folder in '{parent or 'Root'}'", callback_data=f"adm_addsub_{parent}")],
            [InlineKeyboardButton(f"« Back to '{parent or 'All Categories'}'", callback_data=f"adm_dir_{parent}")]
        ]
        await update.message.reply_text(
            f"✅ **New Folder '{text}' created successfully!**\n\n📁 **Location:** `{new_path}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        keyboard = [
            [InlineKeyboardButton(f"« Back to '{parent or 'All Categories'}'", callback_data=f"adm_dir_{parent}")],
            [InlineKeyboardButton("📂 All Categories", callback_data="adm_categories")]
        ]
async def handle_admin_ebook_subcategory_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    parent = context.user_data.get("admin_eb_subcat_target_parent") or ""
    context.user_data["admin_add_eb_subcat"] = False
    context.user_data.pop("admin_eb_subcat_target_parent", None)

    if text == "/cancel":
        back_kb = [[InlineKeyboardButton(f"« Back to '{parent or 'All Categories'}'", callback_data=f"adm_ebdir_{parent}")] if parent else [InlineKeyboardButton("« All Categories", callback_data="adm_ebooks")]]
        await update.message.reply_text("✕ Aborted.", reply_markup=InlineKeyboardMarkup(back_kb))
        return

    added = db.add_ebook_sub_folder(parent, text)
    new_path = f"{parent} > {text}" if parent else text
    if added:
        keyboard = [
            [InlineKeyboardButton(f"📁 Open '{text}' Folder", callback_data=f"adm_ebdir_{new_path}")],
            [InlineKeyboardButton(f"➕ Add E-Book in '{text}'", callback_data=f"adm_addebook_dir_{new_path}")],
            [InlineKeyboardButton(f"➕ Add Another Sub-Folder in '{parent or 'Root'}'", callback_data="adm_ebfld_addfolder")],
            [InlineKeyboardButton(f"« Back to '{parent or 'All Categories'}'", callback_data=f"adm_ebdir_{parent}")]
        ]
        await update.message.reply_text(
            f"✅ **New E-Book Folder '{text}' created successfully!**\n\n📁 **Location:** `{new_path}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        keyboard = [
            [InlineKeyboardButton(f"« Back to '{parent or 'All Categories'}'", callback_data=f"adm_ebdir_{parent}")],
            [InlineKeyboardButton("📂 All Categories", callback_data="adm_ebooks")]
        ]
        await update.message.reply_text(
            f"ℹ️ Folder '{text}' already exists!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )



# ==================== SUPER EASY COURSE CREATION LOGIC ====================

def parse_quick_course_template(text: str, photo_id: str = "") -> Optional[dict]:
    if not text:
        return None

    patterns = {
        "name": r"^(?:কোর্সের\s*নাম|কোর্স\s*নাম|নাম|course\s*name|course\s*title|title|name)\s*[:：\-]\s*",
        "price": r"^(?:কোর্স\s*মূল্য|মূল্য|ফি|টাকা|price|fee|cost)\s*[:：\-]\s*",
        "category": r"^(?:ক্যাটাগরি|ক্যাটাগরী|ক্লাস|ব্যাচ|category|batch|class)\s*[:：\-]\s*",
        "subcategory": r"^(?:সাব-ক্যাটাগরি|সাব\s*ক্যাটাগরি|সাবক্যাটাগরি|প্রোগ্রাম|subcategory|sub-category|sub_category|program|sub)\s*[:：\-]\s*",
        "link": r"^(?:এক্সেস\s*লিংক|চ্যানেল\s*লিংক|লিংক|access\s*link|channel\s*link|link|url)\s*[:：\-]\s*",
        "description": r"^(?:বিবরণ|কোর্সের\s*বিবরণ|ফিচার্স|ফিচার|details|description|desc|features)\s*[:：\-]\s*",
    }

    lines = text.strip().split("\n")
    data = {}
    current_key = None
    accumulated_lines = []

    def flush_key(key, lines_list):
        content = "\n".join(lines_list).strip()
        if not key:
            return
        if key == "name":
            data["name"] = content
        elif key == "price":
            digits = re.findall(r"\d+", content)
            data["price"] = int(digits[0]) if digits else 0
        elif key == "category":
            data["category"] = content
        elif key == "subcategory":
            data["subcategory"] = content
        elif key == "link":
            data["access_link"] = content
        elif key == "description":
            data["description"] = content

    for line in lines:
        line_s = line.strip()
        if not line_s and not current_key:
            continue

        matched_key = None
        matched_content = ""
        for k, pat in patterns.items():
            m = re.match(pat, line_s, re.IGNORECASE)
            if m:
                matched_key = k
                matched_content = line_s[m.end():]
                break

        if matched_key:
            if current_key:
                flush_key(current_key, accumulated_lines)
            current_key = matched_key
            accumulated_lines = [matched_content] if matched_content else []
        else:
            if current_key:
                accumulated_lines.append(line)

    if current_key:
        flush_key(current_key, accumulated_lines)

    if "name" in data and ("price" in data or "description" in data or "category" in data):
        data["image"] = photo_id or ""
        if "price" not in data:
            data["price"] = 0
        if "description" not in data or not data["description"]:
            data["description"] = "কোর্সের বিস্তারিত তথ্য শীঘ্রই যুক্ত করা হবে।"
        data["features"] = data["description"]
        if "access_link" not in data:
            data["access_link"] = ""
        if "instructor" not in data:
            data["instructor"] = "অভিজ্ঞ শিক্ষকবৃন্দ"
        return data

    return None


async def handle_admin_course_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = msg.text.strip() if msg and msg.text else ""
    caption = msg.caption.strip() if msg and msg.caption else ""
    photo = msg.photo
    user_id = update.effective_user.id
    step = context.user_data.get("course_step")

    if text == "/cancel" or caption == "/cancel":
        context.user_data["admin_add_course"] = False
        context.user_data.pop("new_course", None)
        context.user_data.pop("course_step", None)
        orig = context.user_data.pop("course_origin_callback", "adm_categories")
        back_kb = [[InlineKeyboardButton("« Cancel", callback_data=orig)]]
        await update.message.reply_text("✕ Course creation cancelled.", reply_markup=InlineKeyboardMarkup(back_kb))
        return

    # Check for Quick Template (text or caption)
    check_str = caption if caption else text
    photo_id = photo[-1].file_id if photo else ""

    if step == "init_choice" or not step:
        quick_course = parse_quick_course_template(check_str, photo_id)
        if quick_course:
            context.user_data["new_course"] = quick_course
            if not quick_course.get("category"):
                context.user_data["course_step"] = "category_select"
                await send_admin_category_selector(update.message, context, "📂 **কোর্স ক্যাটাগরি নির্বাচন করুন:**")
                return
            elif not quick_course.get("subcategory"):
                context.user_data["course_step"] = "subcategory"
                await send_admin_subcategory_selector(update.message, context, quick_course["category"])
                return
            else:
                # All fields provided in template -> directly show rich preview!
                await send_admin_course_preview(update.message, context, quick_course)
                return

    # Step-by-Step Interactive Wizard Flow
    new_course = context.user_data.get("new_course", {})

    if step == "init_choice" or step == "name":
        course_name = caption if caption else text
        if not course_name:
            await wizard_edit_or_reply(context, update, "⚠️ অনুগ্রহ করে কোর্সের নাম (Title) লিখে পাঠান:")
            return

        new_course["name"] = course_name
        if photo_id:
            new_course["image"] = photo_id

        context.user_data["new_course"] = new_course
        context.user_data["course_step"] = "price"

        k_cancel = [[InlineKeyboardButton("« Back", callback_data="adm_course_back"), InlineKeyboardButton("✕ Cancel", callback_data="adm_course_cancel")]]
        await wizard_edit_or_reply(context, update,
            f"📖 কোর্সের নাম: **{course_name}**\n\n💰 **ধাপ ২/৪: কোর্সের মূল্য লিখুন (Price in BDT):**\n\n(যেমন: 400 বা ফ্রি কোর্সের জন্য 0 লিখুন)",
            reply_markup=InlineKeyboardMarkup(k_cancel)
        )

    elif step == "price":
        digits = re.findall(r"\d+", text)
        if not digits:
            await wizard_edit_or_reply(context, update, "⚠️ অনুগ্রহ করে সঠিক মূল্য লিখুন (যেমন: 400 বা ফ্রি হলে 0):")
            return

        price_val = int(digits[0])
        new_course["price"] = price_val
        context.user_data["new_course"] = new_course
        context.user_data["course_step"] = "description"

        price_tag = f"৳{price_val}" if price_val > 0 else "বিনামূল্যে (Free) 🎁"
        k_cancel = [[InlineKeyboardButton("« Back", callback_data="adm_course_back"), InlineKeyboardButton("✕ Cancel", callback_data="adm_course_cancel")]]
        await wizard_edit_or_reply(context, update,
            f"💰 মূল্য: **{price_tag}**\n\n📝 **ধাপ ৩/৪: কোর্সের বিস্তারিত বিবরণ (Description) লিখুন:**\n\n💡 শিক্ষক প্যানেল, সিলেবাস এবং কোর্সের বিস্তারিত ফিচার্স লিখুন (একাধিক লাইনে লিখতে পারেন):",
            reply_markup=InlineKeyboardMarkup(k_cancel)
        )

    elif step == "description":
        if not text:
            await wizard_edit_or_reply(context, update, "⚠️ অনুগ্রহ করে কোর্সের বিবরণ (Description) লিখে পাঠান:")
            return

        new_course["description"] = text
        new_course["features"] = text
        new_course["instructor"] = "অভিজ্ঞ শিক্ষকবৃন্দ"
        context.user_data["new_course"] = new_course

        # If category and subcategory were already pre-selected (e.g. from folder contextual add):
        if new_course.get("category") and new_course.get("subcategory"):
            if "access_link" not in new_course:
                context.user_data["course_step"] = "link"
                p_text = f"""📁 **ফোল্ডার:** `{new_course['category']}` ➔ `{new_course['subcategory']}`
━━━━━━━━━━━━━━━━━━━━
🔗 **ধাপ ৪/৪: টেলিগ্রাম প্রাইভেট চ্যানেল বা ড্রাইভ এক্সেস লিংক পাঠান:**

💡 লিংক না থাকলে বা পরে দিতে চাইলে নিচের স্কিপ বাটনে চাপুন:"""
                k_link = [
                    [InlineKeyboardButton("« Back", callback_data="adm_course_back"), InlineKeyboardButton("⏭️ Skip Link (স্কিপ করুন)", callback_data="adm_skip_link")],
                    [InlineKeyboardButton("✕ Cancel", callback_data="adm_course_cancel")]
                ]
                await wizard_edit_or_reply(context, update, p_text, reply_markup=InlineKeyboardMarkup(k_link))
            elif not new_course.get("image"):
                context.user_data["course_step"] = "image"
                p_img = f"""📁 **ফোল্ডার:** `{new_course['category']}` ➔ `{new_course['subcategory']}`
━━━━━━━━━━━━━━━━━━━━
🖼️ **ধাপ ৪/৪: কোর্সের ব্যানার বা ছবি পাঠান:**

💡 ছবি ছাড়া প্রকাশ করতে চাইলে নিচের স্কিপ বাটনে চাপুন:"""
                k_img = [
                    [InlineKeyboardButton("« Back", callback_data="adm_course_back"), InlineKeyboardButton("⏭️ Skip Image (ছবি ছাড়া প্রকাশ)", callback_data="adm_skip_img")],
                    [InlineKeyboardButton("✕ Cancel", callback_data="adm_course_cancel")]
                ]
                await wizard_edit_or_reply(context, update, p_img, reply_markup=InlineKeyboardMarkup(k_img))
            else:
                await send_admin_course_preview(update.message, context, new_course)
        elif new_course.get("category") and not new_course.get("subcategory"):
            subcats = db.get_subcategories(new_course["category"])
            if subcats:
                context.user_data["course_step"] = "subcategory"
                await send_admin_subcategory_selector(update.message, context, new_course["category"])
            else:
                new_course["subcategory"] = "General"
                new_course["program"] = "general"
                context.user_data["course_step"] = "link"
                p_text = f"""📁 **ক্যাটাগরি:** `{new_course['category']}`
━━━━━━━━━━━━━━━━━━━━
🔗 **ধাপ ৪/৪: টেলিগ্রাম প্রাইভেট চ্যানেল বা ড্রাইভ এক্সেস লিংক পাঠান:**

💡 লিংক না থাকলে বা পরে দিতে চাইলে নিচের স্কিপ বাটনে চাপুন:"""
                k_link = [
                    [InlineKeyboardButton("« Back", callback_data="adm_course_back"), InlineKeyboardButton("⏭️ Skip Link (স্কিপ করুন)", callback_data="adm_skip_link")],
                    [InlineKeyboardButton("✕ Cancel", callback_data="adm_course_cancel")]
                ]
                await wizard_edit_or_reply(context, update, p_text, reply_markup=InlineKeyboardMarkup(k_link))
        else:
            context.user_data["course_step"] = "category_select"
            await send_admin_category_selector(update.message, context, "📂 **ধাপ ৪/৬: ক্যাটাগরি নির্বাচন করুন:**")

    elif step == "type_custom_cat":
        if not text:
            await wizard_edit_or_reply(context, update, "⚠️ ক্যাটাগরির নাম লিখে পাঠান:")
            return
        db.add_category(text)
        new_course["category"] = text
        context.user_data["new_course"] = new_course
        context.user_data["course_step"] = "type_custom_sub"

        k_cancel = [[InlineKeyboardButton("« Back", callback_data="adm_course_back"), InlineKeyboardButton("✕ Cancel", callback_data="adm_course_cancel")]]
        await wizard_edit_or_reply(context, update,
            f"✅ নতুন ক্যাটাগরি `{text}` যোগ হয়েছে!\n\n🎯 **ধাপ ৫/৬: এই ক্যাটাগরির জন্য সাব-ক্যাটাগরি লিখুন (যেমন: Academic বা Physics):**",
            reply_markup=InlineKeyboardMarkup(k_cancel)
        )

    elif step == "type_custom_sub":
        if not text:
            await wizard_edit_or_reply(context, update, "⚠️ সাব-ক্যাটাগরির নাম লিখে পাঠান:")
            return
        cat_name = new_course.get("category", "General")
        db.add_subcategory(cat_name, text)
        new_course["subcategory"] = text
        new_course["program"] = text.lower()
        context.user_data["new_course"] = new_course

        if "access_link" not in new_course:
            context.user_data["course_step"] = "link"
            p_text = """🔗 **ধাপ ৬/৭: টেলিগ্রাম প্রাইভেট চ্যানেল বা ড্রাইভ এক্সেস লিংক পাঠান:**
━━━━━━━━━━━━━━━━━━━━
💡 লিংক না থাকলে বা পরে দিতে চাইলে নিচের স্কিপ বাটনে চাপুন:"""
            k_link = [
                [InlineKeyboardButton("« Back", callback_data="adm_course_back"), InlineKeyboardButton("⏭️ Skip Link (স্কিপ করুন)", callback_data="adm_skip_link")],
                [InlineKeyboardButton("✕ Cancel", callback_data="adm_course_cancel")]
            ]
            await wizard_edit_or_reply(context, update, p_text, reply_markup=InlineKeyboardMarkup(k_link))
        elif not new_course.get("image"):
            context.user_data["course_step"] = "image"
            p_img = """🖼️ **ধাপ ৭/৭: কোর্সের ব্যানার বা ছবি পাঠান:**
━━━━━━━━━━━━━━━━━━━━
💡 ছবি ছাড়া প্রকাশ করতে চাইলে নিচের স্কিপ বাটনে চাপুন:"""
            k_img = [
                [InlineKeyboardButton("« Back", callback_data="adm_course_back"), InlineKeyboardButton("⏭️ Skip Image (ছবি ছাড়া প্রকাশ)", callback_data="adm_skip_img")],
                [InlineKeyboardButton("✕ Cancel", callback_data="adm_course_cancel")]
            ]
            await wizard_edit_or_reply(context, update, p_img, reply_markup=InlineKeyboardMarkup(k_img))
        else:
            await send_admin_course_preview(update.message, context, new_course)

    elif step == "link":
        if text and text.lower() != "skip":
            new_course["access_link"] = text
        else:
            new_course["access_link"] = ""
        context.user_data["new_course"] = new_course

        if new_course.get("image"):
            await send_admin_course_preview(update.message, context, new_course)
        else:
            context.user_data["course_step"] = "image"
            p_img = """🖼️ **ধাপ ৭/৭: কোর্সের ব্যানার বা ছবি পাঠান:**
━━━━━━━━━━━━━━━━━━━━
💡 ছবি ছাড়া প্রকাশ করতে চাইলে নিচের স্কিপ বাটনে চাপুন:"""
            k_img = [
                [InlineKeyboardButton("« Back", callback_data="adm_course_back"), InlineKeyboardButton("⏭️ Skip Image (ছবি ছাড়া প্রকাশ)", callback_data="adm_skip_img")],
                [InlineKeyboardButton("✕ Cancel", callback_data="adm_course_cancel")]
            ]
            await wizard_edit_or_reply(context, update, p_img, reply_markup=InlineKeyboardMarkup(k_img))

    elif step == "image":
        if photo:
            new_course["image"] = photo[-1].file_id
        elif text and (text.startswith("http://") or text.startswith("https://")):
            new_course["image"] = text
        else:
            new_course["image"] = ""
        context.user_data["new_course"] = new_course
        await send_admin_course_preview(update.message, context, new_course)

    elif step == "image_edit":
        if photo:
            new_course["image"] = photo[-1].file_id
        elif text and text.lower() == "remove":
            new_course["image"] = ""
        elif text and (text.startswith("http://") or text.startswith("https://")):
            new_course["image"] = text
        context.user_data["new_course"] = new_course
        await send_admin_course_preview(update.message, context, new_course)

    elif step and step.startswith("edit_field_"):
        field = step.replace("edit_field_", "")
        if field == "name":
            new_course["name"] = text
        elif field == "price":
            digits = re.findall(r"\d+", text)
            new_course["price"] = int(digits[0]) if digits else 0
        elif field == "desc":
            new_course["description"] = text
            new_course["features"] = text
        elif field == "link":
            new_course["access_link"] = text if text.lower() != "remove" else ""

        context.user_data["new_course"] = new_course
        await send_admin_course_preview(update.message, context, new_course)


async def handle_admin_course_edit_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip() if update.message.text else ""
    photo = update.message.photo
    cid = context.user_data.get("admin_edit_cid")
    field = context.user_data.get("admin_edit_field")
    user_id = update.effective_user.id

    if text == "/cancel":
        context.user_data.pop("admin_edit_cid", None)
        context.user_data.pop("admin_edit_field", None)
        await update.message.reply_text("✕ Edit cancelled.", reply_markup=main_menu_keyboard(user_id))
        course = db.get_course(cid)
        if course:
            text_menu = f"✏️ **Edit Course: '{course['name']}'**\n━━━━━━━━━━━━━━━━━━━━\nSelect property to edit:"
            keyboard = [
                [InlineKeyboardButton("✏️ Edit Title", callback_data=f"edprop_{cid}_name"), InlineKeyboardButton("💰 Edit Price", callback_data=f"edprop_{cid}_price")],
                [InlineKeyboardButton("📂 Edit Category", callback_data=f"edprop_{cid}_category"), InlineKeyboardButton("🎯 Edit Sub-Category", callback_data=f"edprop_{cid}_subcategory")],
                [InlineKeyboardButton("🖼️ Edit Banner", callback_data=f"edprop_{cid}_image"), InlineKeyboardButton("🔗 Edit Link", callback_data=f"edprop_{cid}_access_link")],
                [InlineKeyboardButton("👨‍🏫 Edit Teacher", callback_data=f"edprop_{cid}_instructor"), InlineKeyboardButton("📝 Edit Details", callback_data=f"edprop_{cid}_description")],
                [InlineKeyboardButton("« Back", callback_data=f"adm_edit_{cid}")]
            ]
            await update.message.reply_text(text_menu, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    course = db.get_course(cid)
    if not course:
        context.user_data.pop("admin_edit_cid", None)
        context.user_data.pop("admin_edit_field", None)
        await update.message.reply_text("❌ Course not found.", reply_markup=main_menu_keyboard(user_id))
        return

    updated_data = {}
    if field == "price":
        try:
            val = int(text)
            updated_data["price"] = val
        except ValueError:
            await update.message.reply_text("⚠️ Please enter a valid number (e.g. 250 or 0):")
            return
    elif field == "image":
        if photo:
            updated_data["image"] = photo[-1].file_id
        elif text.lower() == "remove":
            updated_data["image"] = ""
        elif text:
            updated_data["image"] = text
    elif field == "category":
        updated_data["category"] = text.upper()
    elif field == "subcategory":
        updated_data["subcategory"] = text
        updated_data["program"] = text.lower()
    else:
        if not text:
            await update.message.reply_text("⚠️ Please enter text:")
            return
        updated_data[field] = text

    db.update_course(cid, updated_data)
    context.user_data.pop("admin_edit_cid", None)
    context.user_data.pop("admin_edit_field", None)

    await update.message.reply_text(
        f"✅ **'{course['name']}' updated successfully!**",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Edit this course again", callback_data=f"adm_edit_{cid}")],
            [InlineKeyboardButton("« All Courses", callback_data="adm_list_courses")],
            [InlineKeyboardButton("« Admin Menu", callback_data="adm_main")]
        ])
    )


# ==================== ADMIN E-BOOK MANAGEMENT FLOW ====================

async def show_ebook_edit_dashboard(query, ebook_id: str, notice: str = ""):
    eb = db.get_ebook(ebook_id)
    if not eb:
        await query.answer("❌ E-Book not found!", show_alert=True)
        return

    price_str = f"{eb.get('price', 0)} ৳" if eb.get('price', 0) > 0 else "Free 🎁"
    source = f"📄 PDF File ({eb.get('file_name', 'Document')})" if eb.get("file_id") else (eb.get("access_link") or "None")

    msg = f"""{notice}📖 **E-Book Details & Management**
━━━━━━━━━━━━━━━━━━━━

🆔 **ID:** `{ebook_id}`
📖 **Title:** {eb.get('name', 'N/A')}
💰 **Price:** {price_str}
📂 **Category:** {eb.get('category', 'General')}
📁 **File / Link:** {source}

📝 **Description:**
{eb.get('description', 'প্রিমিয়াম ই-বুক ও স্টাডি মেটেরিয়াল।')}

👇 **Select an option to Edit or Delete:**"""

    keyboard = [
        [
            InlineKeyboardButton("✏️ Edit E-Book", callback_data=f"adm_editeb_{ebook_id}"),
            InlineKeyboardButton("✕ Delete E-Book", callback_data=f"adm_deleb_{ebook_id}")
        ],
        [InlineKeyboardButton("« All E-Books", callback_data="adm_list_ebooks")]
    ]

    if query.message.photo:
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def send_admin_ebook_preview(target, context: ContextTypes.DEFAULT_TYPE, eb_data: dict):
    price = eb_data.get("price", 0)
    price_tag = f"৳{price}" if price > 0 else "বিনামূল্যে (Free) 🎁"
    cat = eb_data.get("category", "General")
    
    if eb_data.get("file_id"):
        delivery = f"📄 সরাসরি Telegram PDF ফাইল (`{eb_data.get('file_name', 'Document.pdf')}`)"
    elif eb_data.get("access_link"):
        delivery = f"🔗 গুগল ড্রাইভ / ডাউনলোড লিংক (`{eb_data.get('access_link')}`)"
    else:
        delivery = "⚠️ কোনো ফাইল বা লিংক যুক্ত করা হয়নি (পরে এডিট করতে পারবেন)"

    msg = f"""🎉 **ই-বুক প্রিভিউ ও নিশ্চিতকরণ (E-Book Preview):**
━━━━━━━━━━━━━━━━━━━━

📖 **নাম:** {eb_data.get('name', 'N/A')}
📂 **ক্যাটাগরি:** `{cat}`
💰 **মূল্য:** **{price_tag}**
📁 **ডেলিভারি মেথড:** {delivery}

📝 **বিবরণ:**
{eb_data.get('description', 'প্রিমিয়াম ই-বুক ও স্টাডি মেটেরিয়াল।')}"""

    keyboard = [
        [InlineKeyboardButton("🚀 Publish E-Book (প্রকাশ করুন)", callback_data="adm_publisheb")],
        [InlineKeyboardButton("✕ Cancel", callback_data="adm_cancel_ebook")]
    ]
    if hasattr(target, "reply_text"):
        await target.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif hasattr(target, "edit_message_text"):
        await target.edit_message_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif hasattr(target, "message") and hasattr(target.message, "reply_text"):
        await target.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif hasattr(target, "effective_message") and hasattr(target.effective_message, "reply_text"):
        await target.effective_message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_admin_ebook_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = msg.text.strip() if msg and msg.text else ""
    caption = msg.caption.strip() if msg and msg.caption else ""
    doc = msg.document
    user_id = update.effective_user.id
    step = context.user_data.get("ebook_step")

    if text == "/cancel" or caption == "/cancel":
        context.user_data["admin_add_ebook"] = False
        context.user_data.pop("new_ebook", None)
        context.user_data.pop("ebook_step", None)
        back_kb = [[InlineKeyboardButton("« Cancel", callback_data="adm_ebooks")]]
        await update.message.reply_text("✕ E-Book creation cancelled.", reply_markup=InlineKeyboardMarkup(back_kb))
        return

    new_ebook = context.user_data.get("new_ebook", {})
    cat = new_ebook.get("category", "General")

    if step == "custom_cat":
        if not text:
            await update.message.reply_text("⚠️ অনুগ্রহ করে ক্যাটাগরির নাম লিখে পাঠান:")
            return
        cat = text
        new_ebook["category"] = cat
        context.user_data["new_ebook"] = new_ebook
        context.user_data["ebook_step"] = "name"

        prompt = f"""📁 **ক্যাটাগরি: `{cat}`**
━━━━━━━━━━━━━━━━━━━━

✨ **ধাপ ১/৪: ই-বুকের নাম (E-Book Title) লিখে পাঠান:**
(যেমন: 📘 HSC Physics Formula Sheet | {cat})

💡 ক্যাটাগরি স্বয়ংক্রিয়ভাবে `{cat}` সেট করা থাকবে।"""
        k_cancel = [[InlineKeyboardButton("✕ Cancel", callback_data="adm_cancel_ebook")]]
        await update.message.reply_text(prompt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_cancel))
        return

    if step == "init_choice" or step == "name":
        name_val = caption if caption else text
        if doc and not name_val:
            name_val = doc.file_name or "New E-Book PDF"
        if not name_val:
            await update.message.reply_text("⚠️ অনুগ্রহ করে ই-বুকের নাম লিখে পাঠান:")
            return

        new_ebook["name"] = name_val
        if doc:
            new_ebook["file_id"] = doc.file_id
            new_ebook["file_name"] = doc.file_name or "document.pdf"

        context.user_data["new_ebook"] = new_ebook
        context.user_data["ebook_step"] = "price"

        k_cancel = [[InlineKeyboardButton("« Back", callback_data="adm_ebook_back"), InlineKeyboardButton("✕ Cancel", callback_data="adm_cancel_ebook")]]
        await update.message.reply_text(
            f"📖 ই-বুকের নাম: **{name_val}**\n\n💰 **ধাপ ২/৪: ই-বুকের মূল্য লিখুন (Price in BDT):**\n\n(যেমন: 50 বা ফ্রি ই-বুকের জন্য 0 লিখুন)",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(k_cancel)
        )

    elif step == "price":
        digits = re.findall(r"\d+", text)
        if not digits:
            await update.message.reply_text("⚠️ অনুগ্রহ করে সঠিক মূল্য লিখুন (যেমন: 50 বা ফ্রি হলে 0):")
            return

        price_val = int(digits[0])
        new_ebook["price"] = price_val
        context.user_data["new_ebook"] = new_ebook
        context.user_data["ebook_step"] = "description"

        price_tag = f"৳{price_val}" if price_val > 0 else "বিনামূল্যে (Free) 🎁"
        k_cancel = [
            [InlineKeyboardButton("« Back", callback_data="adm_ebook_back"), InlineKeyboardButton("⏭️ Skip Description", callback_data="adm_skipeb_desc")],
            [InlineKeyboardButton("✕ Cancel", callback_data="adm_cancel_ebook")]
        ]
        await update.message.reply_text(
            f"💰 মূল্য: **{price_tag}**\n\n📝 **ধাপ ৩/৪: ই-বুকের বিস্তারিত বিবরণ (Description) লিখুন:**\n\n💡 ই-বুকের বিষয়বস্তু বা বৈশিষ্ট্য লিখুন (অথবা স্কিপ করতে নিচের বাটনে চাপুন):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(k_cancel)
        )

    elif step == "description":
        desc_val = text if text and text != "/skip" else "প্রিমিয়াম ই-বুক ও স্টাডি মেটেরিয়াল।"
        new_ebook["description"] = desc_val
        context.user_data["new_ebook"] = new_ebook

        if new_ebook.get("file_id"):
            await send_admin_ebook_preview(update.message, context, new_ebook)
            return

        context.user_data["ebook_step"] = "file_or_link"
        p_text = f"""📁 **ক্যাটাগরি:** `{cat}`
━━━━━━━━━━━━━━━━━━━━
📄 **ধাপ ৪/৪: PDF ফাইলটি সরাসরি আপলোড করুন অথবা ডাউনলোড লিংক পাঠান:**

💡 ফাইল পাঠানোর নিয়ম:
• সরাসরি এই চ্যাটে Telegram **PDF Document** ফাইল সেন্ড করুন (সরাসরি ডাউনলোডের জন্য)।
• অথবা Google Drive / Web ডাউনলোড লিংক লিখে পাঠান।"""
        k_file = [
            [InlineKeyboardButton("« Back", callback_data="adm_ebook_back"), InlineKeyboardButton("⏭️ Skip File (পরে যোগ করবেন)", callback_data="adm_skipeb_file")],
            [InlineKeyboardButton("✕ Cancel", callback_data="adm_cancel_ebook")]
        ]
        await update.message.reply_text(p_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(k_file))

    elif step == "file_or_link":
        if doc:
            new_ebook["file_id"] = doc.file_id
            new_ebook["file_name"] = doc.file_name or "document.pdf"
        elif text and text.lower() != "skip" and text != "/skip":
            if text.startswith("http"):
                new_ebook["access_link"] = text
                new_ebook["download_link"] = text
            else:
                new_ebook["access_link"] = text
        context.user_data["new_ebook"] = new_ebook
        await send_admin_ebook_preview(update.message, context, new_ebook)


async def handle_admin_ebook_edit_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = msg.text.strip() if msg and msg.text else ""
    caption = msg.caption.strip() if msg and msg.caption else ""
    doc = msg.document
    user_id = update.effective_user.id

    eb_id = context.user_data.get("admin_edit_ebid")
    field = context.user_data.get("admin_edit_ebook_field")

    if text == "/cancel" or caption == "/cancel":
        context.user_data.pop("admin_edit_ebid", None)
        context.user_data.pop("admin_edit_ebook_field", None)
        await update.message.reply_text("✕ Edit cancelled.", reply_markup=main_menu_keyboard(user_id))
        eb = db.get_ebook(eb_id)
        if eb:
            text_menu = f"✏️ **Edit E-Book: '{eb.get('name')}'**\n━━━━━━━━━━━━━━━━━━━━\nSelect property to edit:"
            keyboard = [
                [InlineKeyboardButton("✏️ Edit Title", callback_data=f"edebprop_{eb_id}_name"), InlineKeyboardButton("💰 Edit Price", callback_data=f"edebprop_{eb_id}_price")],
                [InlineKeyboardButton("📂 Edit Category", callback_data=f"edebprop_{eb_id}_category"), InlineKeyboardButton("📁 Edit File / Drive Link", callback_data=f"edebprop_{eb_id}_access_link")],
                [InlineKeyboardButton("📝 Edit Description", callback_data=f"edebprop_{eb_id}_description")],
                [InlineKeyboardButton("« Back", callback_data=f"adm_vieweb_{eb_id}")]
            ]
            await update.message.reply_text(text_menu, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if not eb_id or not field:
        return

    eb = db.get_ebook(eb_id)
    if not eb:
        context.user_data.pop("admin_edit_ebid", None)
        context.user_data.pop("admin_edit_ebook_field", None)
        await update.message.reply_text("❌ E-Book not found.", reply_markup=main_menu_keyboard(user_id))
        return

    updated_data = {}
    if field == "price":
        try:
            num_str = "".join(filter(str.isdigit, text))
            updated_data["price"] = int(num_str) if num_str else 0
        except ValueError:
            await update.message.reply_text("⚠️ Please enter a valid number (e.g. 100 or 0):")
            return
    elif field == "access_link":
        if doc:
            updated_data["file_id"] = doc.file_id
            updated_data["file_name"] = doc.file_name or "document.pdf"
            updated_data["access_link"] = ""
            updated_data["download_link"] = ""
        elif text.startswith("http"):
            updated_data["access_link"] = text
            updated_data["download_link"] = text
            updated_data["file_id"] = ""
            updated_data["file_name"] = ""
        elif text.lower() == "remove":
            updated_data["access_link"] = ""
            updated_data["download_link"] = ""
            updated_data["file_id"] = ""
            updated_data["file_name"] = ""
        else:
            updated_data["access_link"] = text
            updated_data["download_link"] = text
    else:
        if not text:
            await update.message.reply_text("⚠️ Please enter text:")
            return
        updated_data[field] = text

    db.update_ebook(eb_id, updated_data)
    context.user_data.pop("admin_edit_ebid", None)
    context.user_data.pop("admin_edit_ebook_field", None)

    await update.message.reply_text(
        f"✅ **E-Book property '{field}' updated successfully!**",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Edit this E-Book again", callback_data=f"adm_vieweb_{eb_id}")],
            [InlineKeyboardButton("« All E-Books", callback_data="adm_list_ebooks")],
            [InlineKeyboardButton("« Admin Menu", callback_data="adm_main")]
        ])
    )


# ==================== CHECK ORDER STATUS ====================

async def check_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("📌 **ব্যবহার:** `/status ORDER_ID`\nযেমন: `/status ORD-1001` বা `/status 1001`", parse_mode="Markdown")
        return

    order_id = context.args[0].strip()
    order = db.get_order(order_id)

    if not order:
        await update.message.reply_text("❌ Order not found.")
        return

    user_id = update.effective_user.id
    if order.get("user_id") != user_id and not is_admin(user_id):
        await update.message.reply_text("⛔ You are not authorized to view this order.")
        return

    status_bn = "⏳ Pending" if order['status'] == 'pending' else "✅ Approved" if order['status'] == 'approved' else "❌ Rejected"

    msg = f"""📦 **Order Status**
━━━━━━━━━━━━━━━━━━━━

🆔 **Order ID:** `{format_order_id_display(order_id)}`
📖 **Course:** {order.get('course_name', 'N/A')}
💰 **Amount:** {order['amount']} ৳
💳 **Method:** {order.get('payment_method', 'N/A')}
🔑 **TrxID:** `{order.get('trxid', 'N/A')}`
📅 **Time:** {order.get('date', 'N/A')[:19]}
📌 **Status:** {status_bn}"""

    if order['status'] == 'approved':
        c_obj = db.get_course(order.get('course_id', ''))
        if c_obj and c_obj.get('access_link'):
            msg += f"\n\n🔗 **Access Link:** {c_obj['access_link']}"

    await update.message.reply_text(msg, parse_mode="Markdown")


# ==================== MAIN INITIALIZATION ====================

async def post_init(application: Application) -> None:
    try:
        desc = db.get_setting("bot_description", DEFAULT_BOT_DESCRIPTION)
        await application.bot.set_my_description(description=desc)
        from telegram import BotCommand
        await application.bot.set_my_commands([BotCommand("start", "Start or refresh the bot")])
    except Exception as e:
        logger.error(f"Could not set bot description or commands on startup: {e}")

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("home", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("stop", cancel_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("status", check_status))
    app.add_handler(CommandHandler("courses", lambda u, c: show_categories(u, c)))
    app.add_handler(CommandHandler("mycourses", lambda u, c: show_my_courses(u, c)))
    app.add_handler(CommandHandler("ebooks", lambda u, c: show_my_ebooks(u, c)))
    app.add_handler(CommandHandler("profile", lambda u, c: show_profile(u, c)))
    app.add_handler(CommandHandler("cart", lambda u, c: show_cart(u, c)))
    app.add_handler(CommandHandler("help", lambda u, c: show_info(u, c)))
    app.add_handler(CommandHandler("info", lambda u, c: show_info(u, c)))

    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Text, Photo, Video, Document & Forwarded Messages
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.TEXT | filters.FORWARDED,
        handle_user_input
    ))

    print(f"==================================================")
    print(f"🚀 {BOT_NAME} course selling bot started successfully!")
    print(f"🤖 Bot Username: @{BOT_USERNAME}")
    print(f"📱 Send /start on Telegram to begin.")
    print(f"==================================================")

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
