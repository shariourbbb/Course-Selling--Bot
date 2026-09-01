import json
import math
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from config import (
    COURSES_DB, USERS_DB, ORDERS_DB, COUPONS_DB, CART_DB,
    CATEGORIES_DB, EBOOK_CATEGORIES_DB, EBOOKS_DB, WITHDRAWALS_DB, PAYMENTS_DB,
    ADMINS_DB, ADMIN_PERMISSIONS_DB, ADMIN_IDS, PAYMENT_METHODS, KEYBOARDS_DB, SETTINGS_DB
)

ADMIN_PERMISSION_DEFINITIONS = {
    "course_manage": {"name": "Course Manage", "emoji": "📘"},
    "category_manage": {"name": "Category Manage", "emoji": "📁"},
    "orders": {"name": "Orders", "emoji": "📋"},
    "coupon": {"name": "Coupon", "emoji": "🎟"},
    "payment_settings": {"name": "Payment Settings", "emoji": "💳"},
    "user_manage": {"name": "User", "emoji": "👤"},
    "broadcast": {"name": "Broadcast", "emoji": "📢"},
    "admin_manage": {"name": "Admin Manage", "emoji": "👑"},
    "statistics": {"name": "Statistics", "emoji": "📊"},
    "bot_settings": {"name": "Bot Settings", "emoji": "⚙️"},
    "admin_permission": {"name": "Admin Permission", "emoji": "🔐"},
    "info_manage": {"name": "Info Manage", "emoji": "ℹ️"},
    "ebook_manage": {"name": "E-Book Manage", "emoji": "📖"}
}


class Database:
    def __init__(self):
        self.courses = self._load(COURSES_DB)
        self.users = self._load(USERS_DB)
        self.orders = self._load(ORDERS_DB)
        self.coupons = self._load(COUPONS_DB)
        self.cart = self._load(CART_DB)
        self.categories = self._load_categories(CATEGORIES_DB)
        self.ebook_categories = self._load_categories(EBOOK_CATEGORIES_DB)
        self.ebooks = self._load(EBOOKS_DB)
        self.withdrawals = self._load(WITHDRAWALS_DB)
        self.payments = self._load_payments(PAYMENTS_DB)
        self.admins = self._load_admins(ADMINS_DB)
        self.admin_permissions = self._load(ADMIN_PERMISSIONS_DB)
        self.keyboards = self._load_keyboards(KEYBOARDS_DB)
        self.settings = self._load_settings(SETTINGS_DB)

        # Ensure default ebook categories exist if empty
        if not self.ebook_categories:
            default_eb_cats = ["HSC 26", "HSC 27", "HSC 28", "SSC", "Medical", "Varsity", "General"]
            for c in default_eb_cats:
                self.ebook_categories[c] = []
            self._save_raw(self.ebook_categories, EBOOK_CATEGORIES_DB)

    def _load(self, filename: str) -> dict:
        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading {filename}: {e}")
                return {}
        return {}

    def _load_categories(self, filename: str) -> dict:
        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    cats = json.load(f)
                    if isinstance(cats, dict):
                        return cats
                    elif isinstance(cats, list) and cats:
                        # Migrate list to dict with default subcategories
                        migrated = {}
                        for c in cats:
                            migrated[c] = ["একাডেমিক (Academy)", "রিভিশন (Revision)", "স্পেশাল (Special)"]
                        self._save_raw(migrated, filename)
                        return migrated
            except Exception as e:
                print(f"Error loading {filename}: {e}")

        defaults = {}
        self._save_raw(defaults, filename)
        return defaults

    def _load_payments(self, filename: str) -> dict:
        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    p = json.load(f)
                    if isinstance(p, dict) and "methods" in p:
                        return p
            except Exception as e:
                print(f"Error loading {filename}: {e}")

        defaults = {
            "methods": {
                "bkash": {"name": "bKash", "number": "01XXXXXXXXX", "instruction": "Personal (Send Money)", "status": "active"},
                "nagad": {"name": "Nagad", "number": "01XXXXXXXXX", "instruction": "Personal (Send Money)", "status": "active"},
                "rocket": {"name": "Rocket", "number": "01XXXXXXXXX", "instruction": "Personal (Send Money)", "status": "active"}
            },
            "note": "⚠️ টাকা পাঠানোর পর অবশ্যই এসএমএস থেকে TrxID কপি করে বটে সাবমিট করবেন। কোনো সমস্যা হলে সরাসরি এডমিনের সাথে যোগাযোগ করুন।"
        }
        self._save(defaults, filename)
        return defaults

    def _load_admins(self, filename: str) -> list:
        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for aid in ADMIN_IDS:
                            if aid not in data:
                                data.append(aid)
                        return [int(x) for x in data if str(x).isdigit()]
            except Exception as e:
                print(f"Error loading {filename}: {e}")
        admins = list(ADMIN_IDS)
        self._save_raw(admins, filename)
        return admins

    def _save(self, data: dict, filename: str):
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving {filename}: {e}")

    def _save_raw(self, data, filename: str):
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving {filename}: {e}")

    def save_all(self):
        self._save(self.courses, COURSES_DB)
        self._save(self.users, USERS_DB)
        self._save(self.orders, ORDERS_DB)
        self._save(self.coupons, COUPONS_DB)
        self._save(self.cart, CART_DB)
        self._save_raw(self.categories, CATEGORIES_DB)
        self._save(self.ebooks, EBOOKS_DB)
        self._save(self.withdrawals, WITHDRAWALS_DB)
        self._save(self.payments, PAYMENTS_DB)

    # ==================== PAYMENT SETTINGS OPERATIONS ====================
    def get_payment_methods(self, active_only: bool = False) -> List[dict]:
        methods_dict = self.payments.get("methods", {})
        result = []
        for key, val in methods_dict.items():
            if active_only and val.get("status") == "inactive":
                continue
            item = dict(val)
            item["key"] = key
            result.append(item)
        return result

    def get_payment_method(self, key: str) -> Optional[dict]:
        clean_key = key.lower().strip()
        methods = self.payments.get("methods", {})
        if clean_key in methods:
            item = dict(methods[clean_key])
            item["key"] = clean_key
            return item
        return None

    def update_payment_method(self, key: str, data: dict):
        clean_key = key.lower().strip()
        if "methods" not in self.payments:
            self.payments["methods"] = {}
        if clean_key in self.payments["methods"]:
            self.payments["methods"][clean_key].update(data)
        else:
            self.payments["methods"][clean_key] = data
        self._save(self.payments, PAYMENTS_DB)

    def add_payment_method(self, name: str, number: str, instruction: str = "Personal (Send Money)"):
        key = name.lower().strip().replace(" ", "_")
        if "methods" not in self.payments:
            self.payments["methods"] = {}
        self.payments["methods"][key] = {
            "name": name.strip(),
            "number": number.strip(),
            "instruction": instruction.strip(),
            "status": "active"
        }
        self._save(self.payments, PAYMENTS_DB)

    def delete_payment_method(self, key: str) -> bool:
        clean_key = key.lower().strip()
        if clean_key in self.payments.get("methods", {}):
            del self.payments["methods"][clean_key]
            self._save(self.payments, PAYMENTS_DB)
            return True
        return False

    def toggle_payment_method_status(self, key: str) -> str:
        clean_key = key.lower().strip()
        m = self.payments.get("methods", {}).get(clean_key)
        if m:
            cur = m.get("status", "active")
            new_st = "inactive" if cur == "active" else "active"
            m["status"] = new_st
            self._save(self.payments, PAYMENTS_DB)
            return new_st
        return "inactive"

    def get_payment_note(self) -> str:
        return self.payments.get("note", "⚠️ টাকা পাঠানোর পর অবশ্যই এসএমএস থেকে TrxID কপি করে বটে সাবমিট করবেন।")

    def set_payment_note(self, note: str):
        self.payments["note"] = note.strip()
        self._save(self.payments, PAYMENTS_DB)

    # ==================== USER & EARNINGS OPERATIONS ====================
    def is_referral_enabled(self) -> bool:
        return self.get_setting("referral_system_enabled", True)

    def toggle_referral_system(self) -> bool:
        current = self.is_referral_enabled()
        new_val = not current
        self.set_setting("referral_system_enabled", new_val)
        return new_val

    def get_referral_reward_amount(self) -> int:
        try:
            return int(self.get_setting("referral_bonus_amount", 50))
        except Exception:
            return 50

    def set_referral_reward_amount(self, amount: int) -> bool:
        try:
            val = max(0, int(amount))
            self.set_setting("referral_bonus_amount", val)
            return True
        except Exception:
            return False

    def add_user(self, user_id: int, username: str, full_name: str, referrer_id: Optional[int] = None) -> Tuple[bool, Optional[int]]:
        """
        Adds or updates a user.
        Returns (is_new_user, valid_referrer_id_if_any)
        """
        uid = str(user_id)
        is_new = False
        valid_referrer = None

        if uid not in self.users:
            is_new = True
            if referrer_id and str(referrer_id) in self.users and referrer_id != user_id and self.is_referral_enabled():
                valid_referrer = referrer_id
                ref_uid = str(referrer_id)
                if "referred_users" not in self.users[ref_uid]:
                    self.users[ref_uid]["referred_users"] = []
                
                # Check if this user was already recorded
                already_in = False
                for r_item in self.users[ref_uid]["referred_users"]:
                    if isinstance(r_item, dict) and r_item.get("user_id") == user_id:
                        already_in = True
                        break
                    elif isinstance(r_item, int) and r_item == user_id:
                        already_in = True
                        break
                
                if not already_in:
                    self.users[ref_uid]["referred_users"].append({
                        "user_id": user_id,
                        "username": username or "",
                        "full_name": full_name or "",
                        "joined_date": str(datetime.now()),
                        "converted": False
                    })

            self.users[uid] = {
                "user_id": user_id,
                "username": username or "",
                "full_name": full_name or "",
                "purchased_courses": [],
                "purchased_ebooks": [],
                "balance": 0,
                "earnings_enabled": False,
                "earnings_history": [],
                "referral_count": 0,
                "referred_users": [],
                "referred_by": valid_referrer,
                "referral_reward_claimed": False,
                "joined_date": str(datetime.now())
            }
            self._save(self.users, USERS_DB)
            return is_new, valid_referrer
        else:
            updated = False
            if username and self.users[uid].get("username") != username:
                self.users[uid]["username"] = username
                updated = True
            if full_name and self.users[uid].get("full_name") != full_name:
                self.users[uid]["full_name"] = full_name
                updated = True
            if "purchased_ebooks" not in self.users[uid]:
                self.users[uid]["purchased_ebooks"] = []
                updated = True
            if "earnings_enabled" not in self.users[uid]:
                self.users[uid]["earnings_enabled"] = False
                updated = True
            if "earnings_history" not in self.users[uid]:
                self.users[uid]["earnings_history"] = []
                updated = True
            if "referral_count" not in self.users[uid]:
                self.users[uid]["referral_count"] = 0
                updated = True
            if "referred_users" not in self.users[uid]:
                self.users[uid]["referred_users"] = []
                updated = True
            if "referral_reward_claimed" not in self.users[uid]:
                self.users[uid]["referral_reward_claimed"] = False
                updated = True
            if updated:
                self._save(self.users, USERS_DB)
            return False, None

    def process_paid_referral_conversion(self, buyer_id: int, order_id: str = "N/A", course_name: str = "") -> Tuple[bool, Optional[int], int, int]:
        """
        Triggered when a referred student buys a paid course.
        Credits the referrer's referral_count, balance, earnings_history, and marks conversion.
        Returns (converted, referrer_id, reward_amount, new_referrer_balance)
        """
        uid = str(buyer_id)
        if uid not in self.users:
            return False, None, 0, 0

        # If an order is found and it is free (0 BDT), do not convert
        order = self.get_order(order_id)
        if order:
            try:
                if int(order.get("amount", 0)) <= 0:
                    return False, None, 0, 0
            except Exception:
                pass

        buyer = self.users[uid]
        referrer_id = buyer.get("referred_by")
        if not referrer_id or buyer.get("referral_reward_claimed"):
            return False, None, 0, 0

        if not self.is_referral_enabled():
            return False, None, 0, 0

        ref_uid = str(referrer_id)
        if ref_uid not in self.users:
            return False, None, 0, 0

        # Mark buyer as claimed
        buyer["referral_reward_claimed"] = True

        referrer = self.users[ref_uid]
        referrer["referral_count"] = referrer.get("referral_count", 0) + 1
        reward_amount = self.get_referral_reward_amount()

        # Update entry in referrer's referred_users list
        ref_list = referrer.get("referred_users", [])
        updated_list = []
        found = False
        for item in ref_list:
            if isinstance(item, dict) and item.get("user_id") == buyer_id:
                item["converted"] = True
                item["conversion_date"] = str(datetime.now())
                item["course_name"] = course_name
                item["order_id"] = order_id
                updated_list.append(item)
                found = True
            elif isinstance(item, int) and item == buyer_id:
                updated_list.append({
                    "user_id": buyer_id,
                    "username": buyer.get("username", ""),
                    "full_name": buyer.get("full_name", ""),
                    "joined_date": buyer.get("joined_date", ""),
                    "converted": True,
                    "conversion_date": str(datetime.now()),
                    "course_name": course_name,
                    "order_id": order_id
                })
                found = True
            else:
                updated_list.append(item)

        if not found:
            updated_list.append({
                "user_id": buyer_id,
                "username": buyer.get("username", ""),
                "full_name": buyer.get("full_name", ""),
                "joined_date": buyer.get("joined_date", ""),
                "converted": True,
                "conversion_date": str(datetime.now()),
                "course_name": course_name,
                "order_id": order_id
            })

        # Add earnings & balance (without auto-enabling cash withdrawals)
        referrer["balance"] = referrer.get("balance", 0) + reward_amount
        if "earnings_history" not in referrer:
            referrer["earnings_history"] = []
        referrer["earnings_history"].append({
            "amount": reward_amount,
            "coupon_code": "REFERRAL_BONUS",
            "order_id": str(order_id),
            "date": str(datetime.now())
        })

        self._save(self.users, USERS_DB)
        return True, referrer_id, reward_amount, referrer["balance"]

    def get_referral_global_stats(self) -> dict:
        total_referrers = 0
        total_joined = 0
        total_converted = 0
        total_balance = 0

        for uid, u in self.users.items():
            ref_c = u.get("referral_count", 0)
            bal = u.get("balance", 0)
            refs = u.get("referred_users", [])
            
            if ref_c > 0 or bal > 0 or len(refs) > 0:
                total_referrers += 1

            total_joined += len(refs)
            total_converted += ref_c
            total_balance += bal

        w_stats = self.get_withdrawal_stats()

        return {
            "is_enabled": self.is_referral_enabled(),
            "bonus_amount": self.get_referral_reward_amount(),
            "total_referrers": total_referrers,
            "total_joined": total_joined,
            "total_converted": total_converted,
            "total_balance": total_balance,
            "total_withdrawn": w_stats.get("approved_amount", 0)
        }

    def get_all_referrers_list(self) -> List[dict]:
        referrers = []
        for uid, u in self.users.items():
            ref_c = u.get("referral_count", 0)
            bal = u.get("balance", 0)
            refs = u.get("referred_users", [])
            if ref_c > 0 or bal > 0 or len(refs) > 0:
                u_copy = dict(u)
                u_copy["user_id"] = int(uid)
                referrers.append(u_copy)

        referrers.sort(key=lambda x: (x.get("referral_count", 0), x.get("balance", 0), len(x.get("referred_users", []))), reverse=True)
        return referrers

    def get_paginated_referrers(self, page: int = 1, per_page: int = 8) -> Tuple[List[dict], int]:
        all_refs = self.get_all_referrers_list()
        total = len(all_refs)
        total_pages = max(1, (total + per_page - 1) // per_page)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        return all_refs[start_idx:end_idx], total_pages

    def search_referral_users(self, query: str) -> List[dict]:
        q = query.strip().lower()
        if not q:
            return []
        matched = []
        for uid, u in self.users.items():
            uname = str(u.get("username", "")).lower()
            fname = str(u.get("full_name", "")).lower()
            if q == str(uid) or q in uname or q in fname:
                u_copy = dict(u)
                u_copy["user_id"] = int(uid)
                matched.append(u_copy)
        return matched

    def get_user(self, user_id: int) -> Optional[dict]:
        return self.users.get(str(user_id))

    def get_all_user_ids(self) -> List[int]:
        return [int(uid) for uid in self.users.keys()]

    def update_user(self, user_id: int, data: dict):
        uid = str(user_id)
        if uid in self.users:
            self.users[uid].update(data)
            self._save(self.users, USERS_DB)

    def store_user_access_link(self, user_id: int, course_id: str, link: str):
        uid = str(user_id)
        if uid not in self.users:
            return
        if "access_links" not in self.users[uid]:
            self.users[uid]["access_links"] = {}
        self.users[uid]["access_links"][course_id] = link
        self._save(self.users, USERS_DB)

    def get_user_access_link(self, user_id: int, course_id: str) -> Optional[str]:
        uid = str(user_id)
        user = self.users.get(uid)
        if not user:
            return None
        return user.get("access_links", {}).get(course_id)

    def is_earnings_enabled(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        if user:
            return user.get("earnings_enabled", False)
        return False

    def enable_user_earnings(self, user_id: int, enable: bool = True):
        uid = str(user_id)
        if uid in self.users:
            self.users[uid]["earnings_enabled"] = enable
            self._save(self.users, USERS_DB)

    def add_earning(self, user_id: int, amount: int, coupon_code: str, order_id: str):
        uid = str(user_id)
        if uid in self.users:
            user = self.users[uid]
            user["balance"] = user.get("balance", 0) + int(amount)
            if "earnings_history" not in user:
                user["earnings_history"] = []
            user["earnings_history"].append({
                "amount": int(amount),
                "coupon_code": coupon_code.upper(),
                "order_id": order_id,
                "date": str(datetime.now())
            })
            self._save(self.users, USERS_DB)

    def get_earnings_history(self, user_id: int) -> List[dict]:
        user = self.get_user(user_id)
        if user:
            hist = user.get("earnings_history", [])
            return sorted(hist, key=lambda x: x.get("date", ""), reverse=True)
        return []

    def deduct_balance(self, user_id: int, amount: int) -> bool:
        uid = str(user_id)
        if uid in self.users:
            current = self.users[uid].get("balance", 0)
            if current >= amount:
                self.users[uid]["balance"] = current - amount
                self._save(self.users, USERS_DB)
                return True
        return False

    def update_balance(self, user_id: int, new_balance: int) -> bool:
        uid = str(user_id)
        if uid in self.users:
            self.users[uid]["balance"] = int(new_balance)
            self._save(self.users, USERS_DB)
            return True
        return False

    def refund_balance(self, user_id: int, amount: int):
        uid = str(user_id)
        if uid in self.users:
            self.users[uid]["balance"] = self.users[uid].get("balance", 0) + amount
            self._save(self.users, USERS_DB)

    def is_purchased(self, user_id: int, course_id: str) -> bool:
        user = self.get_user(user_id)
        if user:
            return course_id in user.get("purchased_courses", [])
        return False

    def add_purchase(self, user_id: int, course_id: str):
        user = self.get_user(user_id)
        if user:
            if "purchased_courses" not in user:
                user["purchased_courses"] = []
            if course_id not in user["purchased_courses"]:
                user["purchased_courses"].append(course_id)
                self._save(self.users, USERS_DB)

    def manual_revoke_course(self, user_id: int, course_id: str):
        user = self.get_user(user_id)
        if user and "purchased_courses" in user:
            if course_id in user["purchased_courses"]:
                user["purchased_courses"].remove(course_id)
                self._save(self.users, USERS_DB)

    def get_user_courses(self, user_id: int) -> List[dict]:
        user = self.get_user(user_id)
        if not user:
            return []
        purchased_ids = user.get("purchased_courses", [])
        result = []
        for cid in purchased_ids:
            course = self.get_course(cid)
            if course:
                c_copy = dict(course)
                c_copy["id"] = cid
                result.append(c_copy)
        return result

    def get_referral_stats(self, user_id: int) -> dict:
        user = self.get_user(user_id)
        if not user:
            return {"referral_count": 0, "balance": 0, "referred_users": []}
        return {
            "referral_count": user.get("referral_count", 0),
            "balance": user.get("balance", 0),
            "referred_users": user.get("referred_users", [])
        }

    # ==================== USER SEARCH & PAGINATION ====================
    def get_paginated_users(self, page: int = 1, per_page: int = 8) -> Tuple[List[dict], int]:
        all_users = list(self.users.values())
        all_users.sort(key=lambda u: u.get("joined_date", ""), reverse=True)
        total = len(all_users)
        total_pages = max(1, (total + per_page - 1) // per_page)
        
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        return all_users[start_idx:end_idx], total_pages

    def search_users(self, query: str) -> List[dict]:
        q = query.strip().lower()
        if not q:
            return []
        results = []
        for uid, user in self.users.items():
            uname = str(user.get("username", "")).lower()
            fname = str(user.get("full_name", "")).lower()
            if q == uid or q in uname or q in fname:
                results.append(user)
        return results

    # ==================== EBOOK OPERATIONS ====================
    def get_all_ebooks(self) -> dict:
        return self.ebooks

    def get_ebook(self, ebook_id: str) -> Optional[dict]:
        eb = self.ebooks.get(ebook_id)
        if eb:
            eb_copy = dict(eb)
            eb_copy["id"] = ebook_id
            return eb_copy
        return None

    def add_ebook(self, ebook_id: str, data: dict):
        self.ebooks[ebook_id] = data
        self._save(self.ebooks, EBOOKS_DB)

    def update_ebook(self, ebook_id: str, data: dict):
        if ebook_id in self.ebooks:
            self.ebooks[ebook_id].update(data)
            self._save(self.ebooks, EBOOKS_DB)

    def delete_ebook(self, ebook_id: str) -> bool:
        if ebook_id in self.ebooks:
            del self.ebooks[ebook_id]
            self._save(self.ebooks, EBOOKS_DB)
            return True
        return False

    def is_ebook_purchased(self, user_id: int, ebook_id: str) -> bool:
        user = self.get_user(user_id)
        if user:
            return ebook_id in user.get("purchased_ebooks", [])
        return False

    def has_user_ebook_access(self, user_id: int, ebook_id: str) -> bool:
        eb = self.get_ebook(ebook_id)
        if not eb:
            return False
        if eb.get("price", 0) == 0:
            return True
        return self.is_ebook_purchased(user_id, ebook_id)

    def get_ebooks_by_category(self, category: str = None) -> List[dict]:
        result = []
        for eid, eb in self.ebooks.items():
            if eb.get("status") == "inactive":
                continue
            eb_cat = str(eb.get("category", "General")).strip().lower()
            if category and str(category).strip().upper() != "ALL":
                target = str(category).strip().lower()
                if eb_cat != target and target not in eb_cat and eb_cat not in target:
                    continue
            e = dict(eb)
            e["id"] = eid
            result.append(e)
        return result

    # ==================== EBOOK CATEGORY & DIRECTORY OPERATIONS ====================
    def is_ebook_category_active(self, folder_path: str) -> bool:
        clean_path = str(folder_path).strip().replace(" / ", " > ").replace("/", " > ").lower()
        if not clean_path:
            return True
        inactive_list = [str(x).strip().replace(" / ", " > ").replace("/", " > ").lower() for x in self.get_setting("inactive_ebook_categories", [])]
        if clean_path in inactive_list:
            return False
        segments = clean_path.split(" > ")
        for i in range(1, len(segments) + 1):
            parent_sub = " > ".join(segments[:i])
            if parent_sub in inactive_list:
                return False
        return True

    def toggle_ebook_category_status(self, folder_path: str) -> bool:
        clean_path = str(folder_path).strip().replace(" / ", " > ").replace("/", " > ")
        if not clean_path:
            return True
        clean_lower = clean_path.lower()
        raw_list = list(self.get_setting("inactive_ebook_categories", []))
        inactive_map = {str(x).strip().replace(" / ", " > ").replace("/", " > ").lower(): str(x) for x in raw_list}
        if clean_lower in inactive_map:
            raw_list = [x for x in raw_list if str(x).strip().replace(" / ", " > ").replace("/", " > ").lower() != clean_lower]
            self.set_setting("inactive_ebook_categories", raw_list)
            return True
        else:
            raw_list.append(clean_path)
            self.set_setting("inactive_ebook_categories", raw_list)
            return False

    def get_ebook_sub_folders(self, parent_path: str = "", include_inactive: bool = True) -> List[str]:
        clean_parent = str(parent_path).strip().replace(" / ", " > ").replace("/", " > ")
        if not isinstance(self.ebook_categories, dict):
            return []

        if not clean_parent:
            top_level = []
            for k in self.ebook_categories.keys():
                if " > " not in k:
                    if include_inactive or self.is_ebook_category_active(k):
                        top_level.append(k)
            return top_level

        for k, sub_list in self.ebook_categories.items():
            if k.strip().lower() == clean_parent.lower():
                if isinstance(sub_list, list):
                    items = [str(s).strip() for s in sub_list if str(s).strip()]
                    if include_inactive:
                        return items
                    return [s for s in items if self.is_ebook_category_active(f"{clean_parent} > {s}")]
                return []

        return []

    def add_ebook_sub_folder(self, parent_path: str, folder_name: str) -> bool:
        clean_parent = str(parent_path).strip().replace(" / ", " > ").replace("/", " > ")
        clean_name = str(folder_name).strip()
        if not clean_name:
            return False

        if not isinstance(self.ebook_categories, dict):
            self.ebook_categories = {}

        if not clean_parent:
            if clean_name not in self.ebook_categories:
                self.ebook_categories[clean_name] = []
                self._save_raw(self.ebook_categories, EBOOK_CATEGORIES_DB)
                return True
            return False

        found_key = None
        for k in self.ebook_categories.keys():
            if k.strip().lower() == clean_parent.lower():
                found_key = k
                break

        if not found_key:
            found_key = clean_parent
            self.ebook_categories[found_key] = []

        if not isinstance(self.ebook_categories[found_key], list):
            self.ebook_categories[found_key] = []

        existing_lower = [s.strip().lower() for s in self.ebook_categories[found_key]]
        if clean_name.lower() in existing_lower:
            return False

        self.ebook_categories[found_key].append(clean_name)
        new_full_path = f"{found_key} > {clean_name}"
        if new_full_path not in self.ebook_categories:
            self.ebook_categories[new_full_path] = []

        self._save_raw(self.ebook_categories, EBOOK_CATEGORIES_DB)
        return True

    def delete_ebook_sub_folder(self, parent_path: str, folder_name: str) -> bool:
        clean_parent = str(parent_path).strip().replace(" / ", " > ").replace("/", " > ")
        clean_name = str(folder_name).strip()
        if not isinstance(self.ebook_categories, dict):
            return False

        if not clean_parent:
            deleted = False
            for k in list(self.ebook_categories.keys()):
                if k.strip().lower() == clean_name.lower() or k.strip().lower().startswith(clean_name.lower() + " > "):
                    del self.ebook_categories[k]
                    deleted = True
            if deleted:
                self._save_raw(self.ebook_categories, EBOOK_CATEGORIES_DB)
            return deleted

        deleted = False
        target_parent_key = None
        for k, sub_list in self.ebook_categories.items():
            if k.strip().lower() == clean_parent.lower() and isinstance(sub_list, list):
                target_parent_key = k
                for s in list(sub_list):
                    if s.strip().lower() == clean_name.lower():
                        sub_list.remove(s)
                        deleted = True

        full_child_path = f"{clean_parent} > {clean_name}".lower()
        for k in list(self.ebook_categories.keys()):
            if k.strip().lower() == full_child_path or k.strip().lower().startswith(full_child_path + " > "):
                del self.ebook_categories[k]
                deleted = True

        if deleted:
            self._save_raw(self.ebook_categories, EBOOK_CATEGORIES_DB)
        return deleted

    def move_ebook_folder_order(self, parent_path: str, folder_name: str, direction: str) -> bool:
        clean_parent = str(parent_path).strip().replace(" / ", " > ").replace("/", " > ")
        clean_name = str(folder_name).strip()
        if not isinstance(self.ebook_categories, dict):
            return False

        if not clean_parent:
            keys = list(self.ebook_categories.keys())
            if clean_name not in keys:
                return False
            idx = keys.index(clean_name)
            if direction == "up" and idx > 0:
                keys[idx], keys[idx-1] = keys[idx-1], keys[idx]
            elif direction == "down" and idx < len(keys) - 1:
                keys[idx], keys[idx+1] = keys[idx+1], keys[idx]
            else:
                return False
            new_cats = {}
            for k in keys:
                new_cats[k] = self.ebook_categories[k]
            self.ebook_categories = new_cats
            self._save_raw(self.ebook_categories, EBOOK_CATEGORIES_DB)
            return True

        for k, sub_list in self.ebook_categories.items():
            if k.strip().lower() == clean_parent.lower() and isinstance(sub_list, list):
                if clean_name not in sub_list:
                    return False
                idx = sub_list.index(clean_name)
                if direction == "up" and idx > 0:
                    sub_list[idx], sub_list[idx-1] = sub_list[idx-1], sub_list[idx]
                elif direction == "down" and idx < len(sub_list) - 1:
                    sub_list[idx], sub_list[idx+1] = sub_list[idx+1], sub_list[idx]
                else:
                    return False
                self._save_raw(self.ebook_categories, EBOOK_CATEGORIES_DB)
                return True
        return False

    def move_ebook_folder_path(self, old_path: str, new_path: str) -> bool:
        old_path = old_path.strip().replace(" / ", " > ").replace("/", " > ")
        new_path = new_path.strip().replace(" / ", " > ").replace("/", " > ")
        if not old_path or not new_path or old_path == new_path:
            return False

        old_segments = old_path.split(" > ")
        old_parent_path = " > ".join(old_segments[:-1])
        old_base = old_segments[-1]

        new_segments = new_path.split(" > ")
        new_parent_path = " > ".join(new_segments[:-1])
        new_base = new_segments[-1]

        if not old_parent_path:
            pass
        else:
            for k, sub_list in list(self.ebook_categories.items()):
                if k.strip().lower() == old_parent_path.lower() and isinstance(sub_list, list):
                    if old_base in sub_list:
                        sub_list.remove(old_base)

        if not new_parent_path:
            if new_base not in self.ebook_categories:
                self.ebook_categories[new_base] = []
        else:
            target_key = None
            for k in self.ebook_categories.keys():
                if k.strip().lower() == new_parent_path.lower():
                    target_key = k
                    break
            if not target_key:
                target_key = new_parent_path
                self.ebook_categories[target_key] = []
            if not isinstance(self.ebook_categories[target_key], list):
                self.ebook_categories[target_key] = []
            if new_base not in self.ebook_categories[target_key]:
                self.ebook_categories[target_key].append(new_base)

        new_categories = {}
        for k, v in self.ebook_categories.items():
            if k == old_path:
                new_categories[new_path] = v
            elif k.startswith(old_path + " > "):
                suffix = k[len(old_path):]
                new_categories[new_path + suffix] = v
            else:
                new_categories[k] = v
        self.ebook_categories = new_categories

        # Update all ebooks whose folder_path is under old_path
        for eid, eb in self.ebooks.items():
            eb_fld = str(eb.get("folder_path", "")).strip().replace(" / ", " > ").replace("/", " > ")
            if eb_fld == old_path:
                eb["folder_path"] = new_path
                segs = new_path.split(" > ")
                eb["category"] = segs[0] if len(segs) > 0 else ""
                eb["subcategory"] = segs[1] if len(segs) > 1 else ""
            elif eb_fld.startswith(old_path + " > "):
                suffix = eb_fld[len(old_path):]
                updated_fld = new_path + suffix
                eb["folder_path"] = updated_fld
                segs = updated_fld.split(" > ")
                eb["category"] = segs[0] if len(segs) > 0 else ""
                eb["subcategory"] = segs[1] if len(segs) > 1 else ""

        self._save_raw(self.ebook_categories, EBOOK_CATEGORIES_DB)
        self._save(self.ebooks, EBOOKS_DB)
        return True

    def move_ebook_folder_left(self, parent_path: str, folder_name: str) -> bool:
        clean_parent = str(parent_path).strip().replace(" / ", " > ").replace("/", " > ")
        clean_name = str(folder_name).strip()
        if not clean_parent:
            return False
        segments = [s.strip() for s in clean_parent.split(" > ") if s.strip()]
        new_parent_path = " > ".join(segments[:-1]) if len(segments) > 1 else ""
        old_full_path = f"{clean_parent} > {clean_name}"
        new_full_path = f"{new_parent_path} > {clean_name}" if new_parent_path else clean_name
        return self.move_ebook_folder_path(old_full_path, new_full_path)

    def move_ebook_folder_right(self, parent_path: str, folder_name: str, target_sibling: str) -> bool:
        clean_parent = str(parent_path).strip().replace(" / ", " > ").replace("/", " > ")
        clean_name = str(folder_name).strip()
        clean_sibling = str(target_sibling).strip()
        if clean_name == clean_sibling:
            return False
        old_full_path = f"{clean_parent} > {clean_name}" if clean_parent else clean_name
        new_full_path = f"{clean_parent} > {clean_sibling} > {clean_name}" if clean_parent else f"{clean_sibling} > {clean_name}"
        return self.move_ebook_folder_path(old_full_path, new_full_path)

    def rename_ebook_folder(self, parent_path: str, old_name: str, new_name: str) -> bool:
        clean_parent = str(parent_path).strip().replace(" / ", " > ").replace("/", " > ")
        clean_old = str(old_name).strip()
        clean_new = str(new_name).strip()
        if not clean_new or not clean_old:
            return False
        if not isinstance(self.ebook_categories, dict):
            return False

        if not clean_parent:
            if clean_old not in self.ebook_categories:
                return False
            if clean_new in self.ebook_categories:
                return False
            new_cats = {}
            for k, v in self.ebook_categories.items():
                if k == clean_old:
                    new_cats[clean_new] = v
                else:
                    new_cats[k] = v
            old_child_prefix = f"{clean_old} > "
            for k, v in list(new_cats.items()):
                if k.startswith(old_child_prefix):
                    new_key = clean_new + k[len(old_child_prefix):]
                    new_cats[new_key] = v
                    del new_cats[k]
            self.ebook_categories = new_cats
            self._save_raw(self.ebook_categories, EBOOK_CATEGORIES_DB)
            return True

        for k, sub_list in self.ebook_categories.items():
            if k.strip().lower() == clean_parent.lower() and isinstance(sub_list, list):
                if clean_old not in sub_list:
                    return False
                if clean_new in sub_list:
                    return False
                idx = sub_list.index(clean_old)
                sub_list[idx] = clean_new
                old_child_path = f"{clean_parent} > {clean_old}"
                new_child_path = f"{clean_parent} > {clean_new}"
                for ck in list(self.ebook_categories.keys()):
                    if ck == old_child_path:
                        self.ebook_categories[new_child_path] = self.ebook_categories.pop(ck)
                    elif ck.startswith(old_child_path + " > "):
                        new_ck = new_child_path + ck[len(old_child_path):]
                        self.ebook_categories[new_ck] = self.ebook_categories.pop(ck)
                self._save_raw(self.ebook_categories, EBOOK_CATEGORIES_DB)
                return True
        return False

    def get_ebooks_by_folder(self, folder_path: str, include_inactive: bool = False) -> List[dict]:
        clean_path = str(folder_path).strip().replace(" / ", " > ").replace("/", " > ")
        if not clean_path:
            return [dict(eb, id=eid) for eid, eb in self.ebooks.items() if include_inactive or eb.get("status") != "inactive"]

        segments = [s.strip().lower() for s in clean_path.split(" > ") if s.strip()]
        result = []
        for eid, eb in self.ebooks.items():
            if not include_inactive and eb.get("status") == "inactive":
                continue
            eb_fld = str(eb.get("folder_path", "")).strip().replace(" / ", " > ").replace("/", " > ").lower()
            eb_cat = str(eb.get("category", "")).strip().lower()
            eb_sub = str(eb.get("subcategory", "")).strip().lower()

            if eb_fld:
                if eb_fld == clean_path.lower():
                    e = dict(eb)
                    e["id"] = eid
                    result.append(e)
                continue

            if len(segments) == 1:
                if eb_cat == segments[0] or segments[0] in eb_cat:
                    e = dict(eb)
                    e["id"] = eid
                    result.append(e)
            elif len(segments) == 2:
                if (eb_cat == segments[0] or segments[0] in eb_cat) and (eb_sub == segments[1] or segments[1] in eb_sub):
                    e = dict(eb)
                    e["id"] = eid
                    result.append(e)
            elif len(segments) >= 3:
                if (eb_cat == segments[0] or segments[0] in eb_cat) and any(seg in f"{eb_sub} {eb_fld}" for seg in segments[1:]):
                    e = dict(eb)
                    e["id"] = eid
                    result.append(e)
        return result

    def get_ebook_categories(self, include_inactive: bool = True) -> List[str]:
        return self.get_ebook_sub_folders("", include_inactive=include_inactive)

    def get_ebook_subcategories(self, category: str, include_inactive: bool = True) -> List[str]:
        return self.get_ebook_sub_folders(category, include_inactive=include_inactive)

    def add_ebook_category(self, name: str) -> bool:
        return self.add_ebook_sub_folder("", name)

    def delete_ebook_category(self, name: str) -> bool:
        return self.delete_ebook_sub_folder("", name)

    def move_ebook_to_folder(self, ebook_id: str, new_folder_path: str) -> bool:
        eb = self.get_ebook(ebook_id)
        if not eb:
            return False
        new_folder = new_folder_path.strip().replace(" / ", " > ").replace("/", " > ")
        eb["folder_path"] = new_folder
        segs = new_folder.split(" > ")
        eb["category"] = segs[0] if len(segs) > 0 else ""
        eb["subcategory"] = segs[1] if len(segs) > 1 else ""
        self._save(self.ebooks, EBOOKS_DB)
        return True

    def move_ebook_left(self, ebook_id: str) -> bool:
        eb = self.get_ebook(ebook_id)
        if not eb:
            return False
        eb_fld = str(eb.get("folder_path", "")).strip().replace(" / ", " > ").replace("/", " > ")
        if not eb_fld:
            return False
        segments = [s.strip() for s in eb_fld.split(" > ") if s.strip()]
        if len(segments) <= 1:
            return False
        new_fld = " > ".join(segments[:-1])
        return self.move_ebook_to_folder(ebook_id, new_fld)

    def move_ebook_right(self, ebook_id: str, target_folder: str) -> bool:
        eb = self.get_ebook(ebook_id)
        if not eb:
            return False
        eb_fld = str(eb.get("folder_path", "")).strip().replace(" / ", " > ").replace("/", " > ")
        new_fld = f"{eb_fld} > {target_folder}" if eb_fld else target_folder
        return self.move_ebook_to_folder(ebook_id, new_fld)

    def add_ebook_purchase(self, user_id: int, ebook_id: str):
        user = self.get_user(user_id)
        if user:
            if "purchased_ebooks" not in user:
                user["purchased_ebooks"] = []
            if ebook_id not in user["purchased_ebooks"]:
                user["purchased_ebooks"].append(ebook_id)
                self._save(self.users, USERS_DB)

    def get_user_ebooks(self, user_id: int) -> List[dict]:
        user = self.get_user(user_id)
        if not user:
            return []
        purchased_ids = user.get("purchased_ebooks", [])
        result = []
        for eid in purchased_ids:
            eb = self.get_ebook(eid)
            if eb:
                eb_copy = dict(eb)
                eb_copy["id"] = eid
                result.append(eb_copy)
        return result

    # ==================== COURSE OPERATIONS ====================
    def add_course(self, course_id: str, data: dict):
        self.courses[course_id] = data
        cat = data.get("category", "").strip()
        if cat and cat not in self.categories:
            if isinstance(self.categories, dict):
                self.categories[cat] = []
            elif isinstance(self.categories, list):
                self.categories.append(cat)
            self._save_raw(self.categories, CATEGORIES_DB)
        self._save(self.courses, COURSES_DB)

    def get_course(self, course_id: str) -> Optional[dict]:
        course = self.courses.get(course_id)
        if course:
            c = dict(course)
            c["id"] = course_id
            return c
        return None

    def get_all_courses(self) -> dict:
        return self.courses

    def delete_course(self, course_id: str) -> bool:
        if course_id in self.courses:
            del self.courses[course_id]
            self._save(self.courses, COURSES_DB)
            return True
        return False

    def update_course(self, course_id: str, data: dict):
        if course_id in self.courses:
            self.courses[course_id].update(data)
            cat = data.get("category", "").strip()
            if cat and cat not in self.categories:
                if isinstance(self.categories, dict):
                    self.categories[cat] = []
                elif isinstance(self.categories, list):
                    self.categories.append(cat)
                self._save_raw(self.categories, CATEGORIES_DB)
            self._save(self.courses, COURSES_DB)

    def search_courses(self, query: str) -> List[dict]:
        query = query.strip().lower()
        if not query:
            return []

        words = query.split()
        results = []
        for cid, course in self.courses.items():
            if course.get("status") == "inactive":
                continue
            name = str(course.get("name", "")).lower()
            category = str(course.get("category", "")).lower()
            instructor = str(course.get("instructor", "")).lower()
            description = str(course.get("description", "")).lower()
            program = str(course.get("program", "")).lower()

            combined = f"{name} {category} {instructor} {description} {program}"

            if query in combined or all(w in combined for w in words):
                c = dict(course)
                c["id"] = cid
                results.append(c)
                continue

            if any(len(w) >= 3 and w in combined for w in words):
                c = dict(course)
                c["id"] = cid
                results.append(c)
        return results

    # ==================== HIERARCHICAL FOLDER & CATEGORY OPERATIONS ====================
    def is_category_active(self, folder_path: str) -> bool:
        clean_path = str(folder_path).strip().replace(" / ", " > ").replace("/", " > ").lower()
        if not clean_path:
            return True
        inactive_list = [str(x).strip().replace(" / ", " > ").replace("/", " > ").lower() for x in self.get_setting("inactive_categories", [])]
        if clean_path in inactive_list:
            return False
        segments = clean_path.split(" > ")
        for i in range(1, len(segments) + 1):
            parent_sub = " > ".join(segments[:i])
            if parent_sub in inactive_list:
                return False
        return True

    def toggle_category_status(self, folder_path: str) -> bool:
        clean_path = str(folder_path).strip().replace(" / ", " > ").replace("/", " > ")
        if not clean_path:
            return True
        clean_lower = clean_path.lower()
        raw_list = list(self.get_setting("inactive_categories", []))
        inactive_map = {str(x).strip().replace(" / ", " > ").replace("/", " > ").lower(): str(x) for x in raw_list}
        if clean_lower in inactive_map:
            raw_list = [x for x in raw_list if str(x).strip().replace(" / ", " > ").replace("/", " > ").lower() != clean_lower]
            self.set_setting("inactive_categories", raw_list)
            return True
        else:
            raw_list.append(clean_path)
            self.set_setting("inactive_categories", raw_list)
            return False

    def get_sub_folders(self, parent_path: str = "", include_inactive: bool = True) -> List[str]:
        clean_parent = str(parent_path).strip().replace(" / ", " > ").replace("/", " > ")
        if not isinstance(self.categories, dict):
            self.categories = {}
            self._save_raw(self.categories, CATEGORIES_DB)

        if not clean_parent:
            # Root categories (keys without ' > ')
            roots = []
            for k in self.categories.keys():
                if " > " not in k:
                    if include_inactive or self.is_category_active(k):
                        roots.append(k)
            return roots

        # Look for exact path key in self.categories
        for k, sub_list in self.categories.items():
            if k.strip().lower() == clean_parent.lower():
                items = list(sub_list) if isinstance(sub_list, list) else []
                if include_inactive:
                    return items
                return [s for s in items if self.is_category_active(f"{clean_parent} > {s}")]

        # If it's a top-level category (1 segment)
        segments = [s.strip() for s in clean_parent.split(" > ") if s.strip()]
        if len(segments) == 1:
            for k, sub_list in self.categories.items():
                if k.strip().lower() == segments[0].lower():
                    items = list(sub_list) if isinstance(sub_list, list) else []
                    if include_inactive:
                        return items
                    return [s for s in items if self.is_category_active(f"{clean_parent} > {s}")]

        return []

    def add_sub_folder(self, parent_path: str, folder_name: str) -> bool:
        clean_parent = str(parent_path).strip().replace(" / ", " > ").replace("/", " > ")
        clean_name = str(folder_name).strip().replace(">", "").replace("/", "").strip()
        if not clean_name:
            return False

        if not isinstance(self.categories, dict):
            self.categories = {}

        if not clean_parent:
            # Adding a root category
            for k in self.categories.keys():
                if " > " not in k and k.strip().lower() == clean_name.lower():
                    return False
            self.categories[clean_name] = []
            self._save_raw(self.categories, CATEGORIES_DB)
            return True

        # Finding the parent key
        target_parent_key = None
        for k in self.categories.keys():
            if k.strip().lower() == clean_parent.lower():
                target_parent_key = k
                break

        if not target_parent_key:
            target_parent_key = clean_parent
            self.categories[target_parent_key] = []

        if not isinstance(self.categories[target_parent_key], list):
            self.categories[target_parent_key] = []

        for existing in self.categories[target_parent_key]:
            if existing.strip().lower() == clean_name.lower():
                return False

        self.categories[target_parent_key].append(clean_name)

        # Also initialize empty list for child path
        child_path = f"{target_parent_key} > {clean_name}"
        if child_path not in self.categories:
            self.categories[child_path] = []

        self._save_raw(self.categories, CATEGORIES_DB)
        return True

    def delete_sub_folder(self, parent_path: str, folder_name: str) -> bool:
        clean_parent = str(parent_path).strip().replace(" / ", " > ").replace("/", " > ")
        clean_name = str(folder_name).strip().replace(">", "").replace("/", "").strip()
        if not isinstance(self.categories, dict):
            return False

        if not clean_parent:
            # Deleting a root category
            deleted = False
            for k in list(self.categories.keys()):
                if k.strip().lower() == clean_name.lower() or k.strip().lower().startswith(clean_name.lower() + " > "):
                    del self.categories[k]
                    deleted = True
            if deleted:
                self._save_raw(self.categories, CATEGORIES_DB)
            return deleted

        # Deleting from parent list
        deleted = False
        target_parent_key = None
        for k, sub_list in self.categories.items():
            if k.strip().lower() == clean_parent.lower() and isinstance(sub_list, list):
                target_parent_key = k
                for s in list(sub_list):
                    if s.strip().lower() == clean_name.lower():
                        sub_list.remove(s)
                        deleted = True

        # Also delete all child paths under target_parent_key > clean_name
        full_child_path = f"{clean_parent} > {clean_name}".lower()
        for k in list(self.categories.keys()):
            if k.strip().lower() == full_child_path or k.strip().lower().startswith(full_child_path + " > "):
                del self.categories[k]
                deleted = True

        if deleted:
            self._save_raw(self.categories, CATEGORIES_DB)
        return deleted

    def move_folder_order(self, parent_path: str, folder_name: str, direction: str) -> bool:
        clean_parent = str(parent_path).strip().replace(" / ", " > ").replace("/", " > ")
        clean_name = str(folder_name).strip()
        if not isinstance(self.categories, dict):
            return False

        if not clean_parent:
            keys = list(self.categories.keys())
            if clean_name not in keys:
                return False
            idx = keys.index(clean_name)
            if direction == "up" and idx > 0:
                keys[idx], keys[idx-1] = keys[idx-1], keys[idx]
            elif direction == "down" and idx < len(keys) - 1:
                keys[idx], keys[idx+1] = keys[idx+1], keys[idx]
            else:
                return False
            new_cats = {}
            for k in keys:
                new_cats[k] = self.categories[k]
            self.categories = new_cats
            self._save_raw(self.categories, CATEGORIES_DB)
            return True

        for k, sub_list in self.categories.items():
            if k.strip().lower() == clean_parent.lower() and isinstance(sub_list, list):
                if clean_name not in sub_list:
                    return False
                idx = sub_list.index(clean_name)
                if direction == "up" and idx > 0:
                    sub_list[idx], sub_list[idx-1] = sub_list[idx-1], sub_list[idx]
                elif direction == "down" and idx < len(sub_list) - 1:
                    sub_list[idx], sub_list[idx+1] = sub_list[idx+1], sub_list[idx]
                else:
                    return False
                self._save_raw(self.categories, CATEGORIES_DB)
                return True
        return False

    def move_folder_path(self, old_path: str, new_path: str) -> bool:
        old_path = old_path.strip().replace(" / ", " > ").replace("/", " > ")
        new_path = new_path.strip().replace(" / ", " > ").replace("/", " > ")
        if not old_path or not new_path or old_path == new_path:
            return False

        old_segments = old_path.split(" > ")
        old_parent_path = " > ".join(old_segments[:-1])
        old_base = old_segments[-1]

        new_segments = new_path.split(" > ")
        new_parent_path = " > ".join(new_segments[:-1])
        new_base = new_segments[-1]

        # Remove from old parent list
        if not old_parent_path:
            if old_base in self.categories:
                # Root category being moved, we pop it below
                pass
        else:
            for k, sub_list in list(self.categories.items()):
                if k.strip().lower() == old_parent_path.lower() and isinstance(sub_list, list):
                    if old_base in sub_list:
                        sub_list.remove(old_base)

        # Add to new parent list
        if not new_parent_path:
            if new_base not in self.categories:
                self.categories[new_base] = []
        else:
            target_key = None
            for k in self.categories.keys():
                if k.strip().lower() == new_parent_path.lower():
                    target_key = k
                    break
            if not target_key:
                target_key = new_parent_path
                self.categories[target_key] = []
            if not isinstance(self.categories[target_key], list):
                self.categories[target_key] = []
            if new_base not in self.categories[target_key]:
                self.categories[target_key].append(new_base)

        # Update keys in self.categories
        new_categories = {}
        for k, v in self.categories.items():
            if k == old_path:
                new_categories[new_path] = v
            elif k.startswith(old_path + " > "):
                suffix = k[len(old_path):]
                new_categories[new_path + suffix] = v
            else:
                new_categories[k] = v
        self.categories = new_categories

        # Update all courses whose folder_path is under old_path
        for cid, course in self.courses.items():
            c_fld = str(course.get("folder_path", "")).strip().replace(" / ", " > ").replace("/", " > ")
            if c_fld == old_path:
                course["folder_path"] = new_path
                segs = new_path.split(" > ")
                course["category"] = segs[0] if len(segs) > 0 else ""
                course["subcategory"] = segs[1] if len(segs) > 1 else ""
            elif c_fld.startswith(old_path + " > "):
                suffix = c_fld[len(old_path):]
                updated_fld = new_path + suffix
                course["folder_path"] = updated_fld
                segs = updated_fld.split(" > ")
                course["category"] = segs[0] if len(segs) > 0 else ""
                course["subcategory"] = segs[1] if len(segs) > 1 else ""

        self._save_raw(self.categories, CATEGORIES_DB)
        self._save(self.courses, COURSES_DB)
        return True

    def move_folder_left(self, parent_path: str, folder_name: str) -> bool:
        clean_parent = str(parent_path).strip().replace(" / ", " > ").replace("/", " > ")
        clean_name = str(folder_name).strip()
        if not clean_parent:
            return False
        segments = [s.strip() for s in clean_parent.split(" > ") if s.strip()]
        new_parent_path = " > ".join(segments[:-1]) if len(segments) > 1 else ""
        old_full_path = f"{clean_parent} > {clean_name}"
        new_full_path = f"{new_parent_path} > {clean_name}" if new_parent_path else clean_name
        return self.move_folder_path(old_full_path, new_full_path)

    def move_folder_right(self, parent_path: str, folder_name: str, target_sibling: str) -> bool:
        clean_parent = str(parent_path).strip().replace(" / ", " > ").replace("/", " > ")
        clean_name = str(folder_name).strip()
        clean_sibling = str(target_sibling).strip()
        if clean_name == clean_sibling:
            return False
        old_full_path = f"{clean_parent} > {clean_name}" if clean_parent else clean_name
        new_full_path = f"{clean_parent} > {clean_sibling} > {clean_name}" if clean_parent else f"{clean_sibling} > {clean_name}"
        return self.move_folder_path(old_full_path, new_full_path)

    def move_course_to_folder(self, course_id: str, new_folder_path: str) -> bool:
        course = self.get_course(course_id)
        if not course:
            return False
        new_folder = new_folder_path.strip().replace(" / ", " > ").replace("/", " > ")
        course["folder_path"] = new_folder
        segs = new_folder.split(" > ")
        course["category"] = segs[0] if len(segs) > 0 else ""
        course["subcategory"] = segs[1] if len(segs) > 1 else ""
        self._save(self.courses, COURSES_DB)
        return True

    def move_course_left(self, course_id: str) -> bool:
        course = self.get_course(course_id)
        if not course:
            return False
        c_fld = str(course.get("folder_path", "")).strip().replace(" / ", " > ").replace("/", " > ")
        if not c_fld:
            return False
        segments = [s.strip() for s in c_fld.split(" > ") if s.strip()]
        if len(segments) <= 1:
            return False
        new_fld = " > ".join(segments[:-1])
        return self.move_course_to_folder(course_id, new_fld)

    def move_course_right(self, course_id: str, target_folder: str) -> bool:
        course = self.get_course(course_id)
        if not course:
            return False
        c_fld = str(course.get("folder_path", "")).strip().replace(" / ", " > ").replace("/", " > ")
        new_fld = f"{c_fld} > {target_folder}" if c_fld else target_folder
        return self.move_course_to_folder(course_id, new_fld)


    def rename_folder(self, parent_path: str, old_name: str, new_name: str) -> bool:
        clean_parent = str(parent_path).strip().replace(" / ", " > ").replace("/", " > ")
        clean_old = str(old_name).strip()
        clean_new = str(new_name).strip()
        if not clean_new or not clean_old:
            return False
        if not isinstance(self.categories, dict):
            return False

        if not clean_parent:
            if clean_old not in self.categories:
                return False
            if clean_new in self.categories:
                return False
            new_cats = {}
            for k, v in self.categories.items():
                if k == clean_old:
                    new_cats[clean_new] = v
                else:
                    new_cats[k] = v
            old_child_prefix = f"{clean_old} > "
            for k, v in list(new_cats.items()):
                if k.startswith(old_child_prefix):
                    new_key = clean_new + k[len(old_child):]
                    new_cats[new_key] = v
                    del new_cats[k]
            self.categories = new_cats
            self._save_raw(self.categories, CATEGORIES_DB)
            return True

        for k, sub_list in self.categories.items():
            if k.strip().lower() == clean_parent.lower() and isinstance(sub_list, list):
                if clean_old not in sub_list:
                    return False
                if clean_new in sub_list:
                    return False
                idx = sub_list.index(clean_old)
                sub_list[idx] = clean_new
                old_child_path = f"{clean_parent} > {clean_old}"
                new_child_path = f"{clean_parent} > {clean_new}"
                for ck in list(self.categories.keys()):
                    if ck == old_child_path:
                        self.categories[new_child_path] = self.categories.pop(ck)
                    elif ck.startswith(old_child_path + " > "):
                        new_ck = new_child_path + ck[len(old_child_path):]
                        self.categories[new_ck] = self.categories.pop(ck)
                self._save_raw(self.categories, CATEGORIES_DB)
                return True
        return False

    def get_categories(self, include_inactive: bool = True) -> List[str]:
        return self.get_sub_folders("", include_inactive=include_inactive)

    def get_subcategories(self, category: str, include_inactive: bool = True) -> List[str]:
        return self.get_sub_folders(category, include_inactive=include_inactive)

    def add_category(self, name: str) -> bool:
        return self.add_sub_folder("", name)

    def delete_category(self, name: str) -> bool:
        return self.delete_sub_folder("", name)

    def add_subcategory(self, category: str, subcategory: str) -> bool:
        return self.add_sub_folder(category, subcategory)

    def delete_subcategory(self, category: str, subcategory: str) -> bool:
        return self.delete_sub_folder(category, subcategory)

    def get_courses_by_filter(self, category: str = None, subcategory: str = None, program: str = None, include_inactive: bool = False) -> List[dict]:
        result = []
        for cid, course in self.courses.items():
            if not include_inactive and course.get("status") == "inactive":
                continue
            if category and course.get("category", "").strip().lower() != category.strip().lower():
                continue

            sub_filter = subcategory or program
            if sub_filter and str(sub_filter).strip().upper() != "ALL":
                c_sub = str(course.get("subcategory", "")).strip().lower()
                c_prog = str(course.get("program", "")).strip().lower()
                c_fld = str(course.get("folder_path", "")).strip().replace(" / ", " > ").replace("/", " > ").lower()
                target_sub = str(sub_filter).strip().lower()

                matched = False

                # Exact folder selection should win: select "Admission" => only Admission direct courses
                # and select "HSC 26 > Admission > VARSITY" => nested child folder courses.
                if c_fld:
                    cat_prefix = f"{category.strip().lower()} > " if category else ""
                    if c_fld == target_sub:
                        matched = True
                    elif cat_prefix and c_fld.startswith(f"{cat_prefix}{target_sub} > "):
                        matched = True
                    elif c_fld.startswith(f"{target_sub} > "):
                        matched = True
                    elif cat_prefix and c_fld == f"{cat_prefix}{target_sub}":
                        matched = True
                else:
                    if target_sub in c_sub or target_sub in c_prog or (c_sub and c_sub in target_sub) or (c_prog and c_prog in target_sub):
                        matched = True
                    else:
                        target_words = [w.strip("(),.-_") for w in target_sub.split() if len(w.strip("(),.-_")) >= 3]
                        course_text = f"{c_sub} {c_prog}"
                        if target_words and any(w in course_text for w in target_words):
                            matched = True

                if not matched:
                    continue

            c = dict(course)
            c["id"] = cid
            result.append(c)
        return result

    def get_courses_by_folder(self, folder_path: str, include_inactive: bool = False) -> List[dict]:
        clean_path = str(folder_path).strip().replace(" / ", " > ").replace("/", " > ")
        if not clean_path:
            return [dict(c, id=cid) for cid, c in self.courses.items() if include_inactive or c.get("status") != "inactive"]

        segments = [s.strip().lower() for s in clean_path.split(" > ") if s.strip()]
        result = []
        for cid, course in self.courses.items():
            if not include_inactive and course.get("status") == "inactive":
                continue
            c_fld = str(course.get("folder_path", "")).strip().replace(" / ", " > ").replace("/", " > ").lower()
            c_cat = str(course.get("category", "")).strip().lower()
            c_sub = str(course.get("subcategory", "")).strip().lower()
            c_prog = str(course.get("program", "")).strip().lower()

            # Only exact folder matches should appear in a folder listing.
            # A child folder like "A > B" must not be included in "A".
            if c_fld:
                if c_fld == clean_path.lower():
                    c = dict(course)
                    c["id"] = cid
                    result.append(c)
                continue

            if len(segments) == 1:
                if c_cat == segments[0] or segments[0] in c_cat:
                    c = dict(course)
                    c["id"] = cid
                    result.append(c)
            elif len(segments) == 2:
                if (c_cat == segments[0] or segments[0] in c_cat) and (c_sub == segments[1] or c_prog == segments[1] or segments[1] in c_sub or segments[1] in c_prog):
                    c = dict(course)
                    c["id"] = cid
                    result.append(c)
            elif len(segments) >= 3:
                if (c_cat == segments[0] or segments[0] in c_cat) and any(seg in f"{c_sub} {c_prog} {c_fld}" for seg in segments[1:]):
                    c = dict(course)
                    c["id"] = cid
                    result.append(c)
        return result

    # ==================== CART OPERATIONS ====================
    def add_to_cart(self, user_id: int, course_id: str) -> bool:
        uid = str(user_id)
        if uid not in self.cart:
            self.cart[uid] = []
        if course_id not in self.cart[uid]:
            self.cart[uid].append(course_id)
            self._save(self.cart, CART_DB)
            return True
        return False

    def remove_from_cart(self, user_id: int, course_id: str) -> bool:
        uid = str(user_id)
        if uid in self.cart and course_id in self.cart[uid]:
            self.cart[uid].remove(course_id)
            self._save(self.cart, CART_DB)
            return True
        return False

    def get_cart(self, user_id: int) -> List[dict]:
        uid = str(user_id)
        course_ids = self.cart.get(uid, [])
        items = []
        for cid in course_ids:
            course = self.get_course(cid)
            if course:
                items.append(course)
        return items

    def clear_cart(self, user_id: int):
        self.cart[str(user_id)] = []
        self._save(self.cart, CART_DB)

    # ==================== ORDER OPERATIONS ====================
    def generate_next_order_id(self) -> str:
        highest = 1000
        for oid in self.orders.keys():
            m = re.match(r"^ORD-(\d+)$", str(oid).strip(), re.IGNORECASE)
            if m:
                try:
                    num = int(m.group(1))
                    if num > highest:
                        highest = num
                except ValueError:
                    pass
        next_num = highest + 1
        new_id = f"ORD-{next_num}"
        while new_id in self.orders:
            next_num += 1
            new_id = f"ORD-{next_num}"
        return new_id

    def add_order(self, order_id: str, data: dict):
        self.orders[order_id] = data
        self._save(self.orders, ORDERS_DB)

    def get_order(self, order_id: str) -> Optional[dict]:
        clean_id = str(order_id).strip()
        if clean_id.startswith("#"):
            clean_id = clean_id[1:]
        order = self.orders.get(clean_id)
        if not order and not clean_id.upper().startswith("ORD-"):
            order = self.orders.get(f"ORD-{clean_id}")
        if not order:
            for k, v in self.orders.items():
                if k.strip().lower() == clean_id.lower() or k.strip().lower() == f"ord-{clean_id.lower()}":
                    order = v
                    clean_id = k
                    break
        if order:
            o = dict(order)
            o["order_id"] = clean_id
            return o
        return None

    def update_order(self, order_id: str, data: dict):
        if order_id in self.orders:
            self.orders[order_id].update(data)
            self._save(self.orders, ORDERS_DB)

    def get_user_orders(self, user_id: int) -> List[dict]:
        result = []
        for oid, order in self.orders.items():
            if order.get("user_id") == user_id:
                o = dict(order)
                o["order_id"] = oid
                result.append(o)
        result.sort(key=lambda x: x.get("date", ""), reverse=True)
        return result

    def get_pending_orders(self) -> List[dict]:
        result = []
        for oid, order in self.orders.items():
            if order.get("status") == "pending":
                o = dict(order)
                o["order_id"] = oid
                result.append(o)
        result.sort(key=lambda x: x.get("date", ""), reverse=True)
        return result

    def get_paginated_pending_orders(self, page: int = 1, per_page: int = 8) -> Tuple[List[dict], int]:
        pending = []
        for oid, order in self.orders.items():
            if order.get("status") == "pending":
                o = dict(order)
                o["order_id"] = oid
                pending.append(o)
        pending.sort(key=lambda x: x.get("date", ""), reverse=True)
        total = len(pending)
        total_pages = max(1, (total + per_page - 1) // per_page)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        return pending[start_idx:end_idx], total_pages

    def get_paginated_all_orders(self, page: int = 1, per_page: int = 8) -> Tuple[List[dict], int]:
        all_orders = []
        for oid, order in self.orders.items():
            o = dict(order)
            o["order_id"] = oid
            all_orders.append(o)
        all_orders.sort(key=lambda x: x.get("date", ""), reverse=True)
        total = len(all_orders)
        total_pages = max(1, (total + per_page - 1) // per_page)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        return all_orders[start_idx:end_idx], total_pages

    # ==================== ADVANCED COUPON & REFERRAL REWARD ====================
    def add_coupon(
        self,
        code: str,
        discount_type: str = "fixed",
        discount_value: int = 0,
        min_purchase: int = 0,
        max_discount: int = 0,
        applicable_category: str = "All",
        applicable_course_id: Optional[str] = None,
        applicable_course_name: Optional[str] = None,
        usage_limit: int = 100,
        per_user_limit: int = 1,
        first_order_only: bool = False,
        status: str = "active",
        enable_referral_reward: bool = False,
        reward_user_id: Optional[int] = None,
        reward_type: str = "fixed",
        reward_amount: int = 0,
        start_date: str = "",
        expiry_date: str = ""
    ):
        clean_code = code.upper().strip()
        self.coupons[clean_code] = {
            "code": clean_code,
            "discount_type": discount_type,
            "discount_value": int(discount_value),
            "discount": int(discount_value),
            "min_purchase": int(min_purchase),
            "max_discount": int(max_discount),
            "applicable_category": applicable_category or "All",
            "applicable_course_id": applicable_course_id,
            "applicable_course_name": applicable_course_name,
            "usage_limit": int(usage_limit),
            "uses": int(usage_limit),
            "per_user_limit": int(per_user_limit),
            "first_order_only": bool(first_order_only),
            "status": status or "active",
            "enable_referral_reward": bool(enable_referral_reward),
            "reward_user_id": int(reward_user_id) if reward_user_id else None,
            "reward_type": reward_type or "fixed",
            "reward_amount": int(reward_amount),
            "start_date": start_date or str(datetime.now())[:10],
            "expiry_date": expiry_date or "None",
            "used_count": self.coupons.get(clean_code, {}).get("used_count", 0),
            "used_by": self.coupons.get(clean_code, {}).get("used_by", {})
        }
        if enable_referral_reward and reward_user_id:
            self.enable_user_earnings(int(reward_user_id), True)

        self._save(self.coupons, COUPONS_DB)

    def validate_coupon_advanced(
        self,
        code: str,
        user_id: int,
        purchase_amount: int,
        category: str = "",
        course_id: str = "",
        course_ids: List[str] = None
    ) -> Tuple[bool, int, str, Optional[dict]]:
        clean_code = code.upper().strip()
        coupon = self.coupons.get(clean_code)
        if not coupon:
            return False, 0, "Coupon not found.", None

        if coupon.get("status", "active") != "active":
            return False, 0, "This coupon is currently inactive.", None

        used_count = coupon.get("used_count", 0)
        usage_limit = coupon.get("usage_limit", coupon.get("uses", 100))
        if used_count >= usage_limit:
            return False, 0, "Coupon usage limit reached.", None

        uid_str = str(user_id)
        used_by = coupon.get("used_by", {})
        if isinstance(used_by, list):
            used_by = {str(u): 1 for u in used_by}
            coupon["used_by"] = used_by
        user_uses = used_by.get(uid_str, 0)
        per_user_limit = coupon.get("per_user_limit", 1)
        if user_uses >= per_user_limit:
            if per_user_limit > 1:
                return False, 0, f"Coupon already used (Max {per_user_limit} times).", None
            return False, 0, "You have already used this coupon.", None

        min_p = coupon.get("min_purchase", 0)
        if min_p > 0 and purchase_amount < min_p:
            return False, 0, f"Minimum order of ৳{min_p} required.", None

        # Check Specific Course Scope
        app_cid = coupon.get("applicable_course_id")
        if app_cid:
            c_name = coupon.get("applicable_course_name") or "selected course"
            if course_ids:
                if app_cid not in course_ids:
                    return False, 0, f"Valid only for '{c_name}'.", None
            elif course_id:
                if app_cid != course_id:
                    return False, 0, f"Valid only for '{c_name}'.", None

        # Check Category Scope
        app_cat = coupon.get("applicable_category", "All")
        if app_cat and app_cat not in ["All", "Specific"] and category:
            if app_cat.strip().lower() != category.strip().lower():
                return False, 0, f"Valid only for '{app_cat}' category.", None

        if coupon.get("first_order_only", False):
            user_orders = self.get_user_orders(user_id)
            approved_orders = [o for o in user_orders if o.get("status") == "approved"]
            if approved_orders:
                return False, 0, "Valid only for first order.", None

        dtype = coupon.get("discount_type", "fixed")
        dval = coupon.get("discount_value", coupon.get("discount", 0))

        if dtype == "percentage":
            calc_disc = int(round(purchase_amount * (dval / 100.0)))
            max_d = coupon.get("max_discount", 0)
            if max_d > 0 and calc_disc > max_d:
                calc_disc = max_d
            final_discount = min(calc_disc, purchase_amount)
        else:
            final_discount = min(dval, purchase_amount)

        return True, final_discount, "Coupon applied successfully!", coupon

    def validate_coupon(self, code: str, user_id: int) -> Optional[int]:
        valid, disc, _, _ = self.validate_coupon_advanced(code, user_id, 999999)
        return disc if valid else None

    def use_coupon(self, code: str, user_id: int):
        clean_code = code.upper().strip()
        coupon = self.coupons.get(clean_code)
        if coupon:
            coupon["used_count"] = coupon.get("used_count", 0) + 1
            coupon["uses"] = max(0, coupon.get("uses", 1) - 1)
            used_by = coupon.get("used_by", {})
            if isinstance(used_by, list):
                used_by = {str(u): 1 for u in used_by}
            uid_str = str(user_id)
            used_by[uid_str] = used_by.get(uid_str, 0) + 1
            coupon["used_by"] = used_by
            self._save(self.coupons, COUPONS_DB)

    def trigger_referral_reward_for_order(self, order: dict) -> Optional[Tuple[int, int, str]]:
        coupon_code = order.get("coupon_code", "").upper().strip()
        if not coupon_code or coupon_code not in self.coupons:
            return None

        coupon = self.coupons[coupon_code]
        if coupon.get("enable_referral_reward") and coupon.get("reward_user_id"):
            reward_uid = int(coupon["reward_user_id"])
            reward_type = coupon.get("reward_type", "fixed")
            raw_amt = float(coupon.get("reward_amount", 0))

            if reward_type == "percentage":
                order_amt = int(order.get("amount", 0))
                reward_amount = max(1, int(round(order_amt * (raw_amt / 100.0))))
            else:
                reward_amount = int(raw_amt)

            if reward_amount > 0:
                self.add_earning(reward_uid, reward_amount, coupon_code, order.get("order_id", "N/A"))
                return (reward_uid, reward_amount, coupon_code)
        return None

    def get_all_coupons(self) -> dict:
        return self.coupons

    def get_coupon(self, code: str) -> Optional[dict]:
        clean_code = code.upper().strip()
        c = self.coupons.get(clean_code)
        if c:
            c_copy = dict(c)
            c_copy["code"] = clean_code
            return c_copy
        return None

    def set_coupon_status(self, code: str, status: str) -> bool:
        clean_code = code.upper().strip()
        if clean_code in self.coupons:
            self.coupons[clean_code]["status"] = status
            self._save(self.coupons, COUPONS_DB)
            return True
        return False

    def deactivate_all_coupons(self) -> int:
        count = 0
        for code, c in self.coupons.items():
            c["status"] = "inactive"
            count += 1
        self._save(self.coupons, COUPONS_DB)
        return count

    def activate_all_coupons(self) -> int:
        count = 0
        for code, c in self.coupons.items():
            c["status"] = "active"
            count += 1
        self._save(self.coupons, COUPONS_DB)
        return count

    def delete_coupon(self, code: str) -> bool:
        clean_code = code.upper().strip()
        if clean_code in self.coupons:
            del self.coupons[clean_code]
            self._save(self.coupons, COUPONS_DB)
            return True
        return False

    # ==================== WITHDRAWAL OPERATIONS ====================
    def add_withdrawal(self, withdraw_id: str, data: dict):
        self.withdrawals[withdraw_id] = data
        self._save(self.withdrawals, WITHDRAWALS_DB)

    def get_withdrawal(self, withdraw_id: str) -> Optional[dict]:
        w = self.withdrawals.get(withdraw_id)
        if w:
            w_copy = dict(w)
            w_copy["withdraw_id"] = withdraw_id
            return w_copy
        return None

    def update_withdrawal(self, withdraw_id: str, data: dict):
        if withdraw_id in self.withdrawals:
            self.withdrawals[withdraw_id].update(data)
            self._save(self.withdrawals, WITHDRAWALS_DB)

    def get_user_withdrawals(self, user_id: int) -> List[dict]:
        result = []
        for wid, w in self.withdrawals.items():
            if w.get("user_id") == user_id:
                w_copy = dict(w)
                w_copy["withdraw_id"] = wid
                result.append(w_copy)
        result.sort(key=lambda x: x.get("date", ""), reverse=True)
        return result

    def get_pending_withdrawals(self) -> List[dict]:
        result = []
        for wid, w in self.withdrawals.items():
            if w.get("status") == "pending":
                w_copy = dict(w)
                w_copy["withdraw_id"] = wid
                result.append(w_copy)
        result.sort(key=lambda x: x.get("date", ""), reverse=True)
        return result

    def get_all_withdrawals(self, status: Optional[str] = None) -> List[dict]:
        result = []
        for wid, w in self.withdrawals.items():
            if status and status != "all":
                if w.get("status") != status:
                    continue
            w_copy = dict(w)
            w_copy["withdraw_id"] = wid
            result.append(w_copy)
        result.sort(key=lambda x: x.get("date", ""), reverse=True)
        return result

    def get_withdrawal_stats(self) -> dict:
        all_w = list(self.withdrawals.values())
        pending_list = [w for w in all_w if w.get("status") == "pending"]
        approved_list = [w for w in all_w if w.get("status") == "approved"]
        rejected_list = [w for w in all_w if w.get("status") == "rejected"]

        pending_amt = sum(w.get("amount", 0) for w in pending_list)
        approved_amt = sum(w.get("amount", 0) for w in approved_list)
        rejected_amt = sum(w.get("amount", 0) for w in rejected_list)
        total_amt = pending_amt + approved_amt

        return {
            "total_count": len(all_w),
            "total_amount": total_amt,
            "pending_count": len(pending_list),
            "pending_amount": pending_amt,
            "approved_count": len(approved_list),
            "approved_amount": approved_amt,
            "rejected_count": len(rejected_list),
            "rejected_amount": rejected_amt,
        }

    def get_paginated_withdrawals(self, status: Optional[str] = None, page: int = 1, per_page: int = 6) -> Tuple[List[dict], int]:
        all_items = self.get_all_withdrawals(status=status)
        total_count = len(all_items)
        total_pages = max(1, math.ceil(total_count / per_page))
        current_page = max(1, min(page, total_pages))
        start_idx = (current_page - 1) * per_page
        end_idx = start_idx + per_page
        return all_items[start_idx:end_idx], total_pages

    def search_withdrawals(self, query: str) -> List[dict]:
        q = query.strip().lower()
        if not q:
            return []
        found = []
        for wid, w in self.withdrawals.items():
            w_user = str(w.get("user_id", "")).lower()
            w_name = str(w.get("full_name", "")).lower()
            w_uname = str(w.get("username", "")).lower()
            w_acc = str(w.get("account", "")).lower()
            w_meth = str(w.get("method", "")).lower()
            if q in wid.lower() or q in w_user or q in w_name or q in w_uname or q in w_acc or q in w_meth:
                w_copy = dict(w)
                w_copy["withdraw_id"] = wid
                found.append(w_copy)
        found.sort(key=lambda x: x.get("date", ""), reverse=True)
        return found

    def approve_withdrawal(self, withdraw_id: str) -> bool:
        w = self.get_withdrawal(withdraw_id)
        if w and w.get("status") == "pending":
            self.update_withdrawal(withdraw_id, {"status": "approved", "processed_date": str(datetime.now())})
            return True
        return False

    def reject_withdrawal(self, withdraw_id: str) -> bool:
        w = self.get_withdrawal(withdraw_id)
        if w and w.get("status") == "pending":
            self.update_withdrawal(withdraw_id, {"status": "rejected", "processed_date": str(datetime.now())})
            self.refund_balance(w["user_id"], w.get("amount", 0))
            return True
        return False

    # ==================== STATS ====================
    def get_stats(self) -> dict:
        total_users = len(self.users)
        total_courses = len(self.courses)
        total_ebooks = len(self.ebooks)
        total_orders = len(self.orders)
        pending_orders = sum(1 for o in self.orders.values() if o.get("status") == "pending")
        pending_withdrawals = sum(1 for w in self.withdrawals.values() if w.get("status") == "pending")
        total_revenue = sum(
            o.get("amount", 0) for o in self.orders.values() if o.get("status") == "approved"
        )
        total_withdrawn = sum(
            w.get("amount", 0) for w in self.withdrawals.values() if w.get("status") == "approved"
        )
        return {
            "total_users": total_users,
            "total_courses": total_courses,
            "total_ebooks": total_ebooks,
            "total_orders": total_orders,
            "pending_orders": pending_orders,
            "pending_withdrawals": pending_withdrawals,
            "total_revenue": total_revenue,
            "total_withdrawn": total_withdrawn
        }

    # ==================== ADMIN ROLE & PERMISSION MANAGEMENT ====================
    def get_admins(self) -> List[int]:
        for aid in ADMIN_IDS:
            if aid not in self.admins:
                self.admins.append(aid)
        return list(self.admins)

    def is_admin(self, user_id: int) -> bool:
        if not user_id:
            return False
        try:
            uid = int(user_id)
        except ValueError:
            return False
        return uid in self.get_admins() or uid in ADMIN_IDS

    def is_super_admin(self, user_id: int) -> bool:
        if not user_id:
            return False
        try:
            uid = int(user_id)
        except ValueError:
            return False
        return len(ADMIN_IDS) > 0 and uid == ADMIN_IDS[0]

    def get_admin_permissions(self, user_id: int) -> dict:
        uid_str = str(user_id)
        if self.is_super_admin(user_id):
            return {k: True for k in ADMIN_PERMISSION_DEFINITIONS.keys()}

        user_perms = self.admin_permissions.get(uid_str)
        if user_perms is None:
            user_perms = {k: True for k in ADMIN_PERMISSION_DEFINITIONS.keys()}
            self.admin_permissions[uid_str] = user_perms
            self._save_raw(self.admin_permissions, ADMIN_PERMISSIONS_DB)
        else:
            updated = False
            for k in ADMIN_PERMISSION_DEFINITIONS.keys():
                if k not in user_perms:
                    user_perms[k] = True
                    updated = True
            if updated:
                self.admin_permissions[uid_str] = user_perms
                self._save_raw(self.admin_permissions, ADMIN_PERMISSIONS_DB)
        return dict(user_perms)

    def has_permission(self, user_id: int, perm_key: str) -> bool:
        if not self.is_admin(user_id):
            return False
        if self.is_super_admin(user_id):
            return True
        perms = self.get_admin_permissions(user_id)
        return bool(perms.get(perm_key, True))

    def toggle_admin_permission(self, user_id: int, perm_key: str) -> bool:
        uid_str = str(user_id)
        perms = self.get_admin_permissions(user_id)
        if perm_key in ADMIN_PERMISSION_DEFINITIONS:
            perms[perm_key] = not perms.get(perm_key, True)
            self.admin_permissions[uid_str] = perms
            self._save_raw(self.admin_permissions, ADMIN_PERMISSIONS_DB)
            return perms[perm_key]
        return False

    def set_admin_permission(self, user_id: int, perm_key: str, value: bool) -> bool:
        uid_str = str(user_id)
        perms = self.get_admin_permissions(user_id)
        if perm_key in ADMIN_PERMISSION_DEFINITIONS:
            perms[perm_key] = bool(value)
            self.admin_permissions[uid_str] = perms
            self._save_raw(self.admin_permissions, ADMIN_PERMISSIONS_DB)
            return True
        return False

    def add_admin(self, user_id: int, added_by: int = 0) -> bool:
        try:
            uid = int(user_id)
        except ValueError:
            return False
        if uid not in self.admins:
            self.admins.append(uid)
            self._save_raw(self.admins, ADMINS_DB)
            # Initialize default permissions
            self.get_admin_permissions(uid)
            return True
        return False

    def remove_admin(self, user_id: int) -> bool:
        try:
            uid = int(user_id)
        except ValueError:
            return False
        if uid in ADMIN_IDS and len(ADMIN_IDS) > 0 and uid == ADMIN_IDS[0]:
            return False  # Protect primary owner
        if uid in self.admins:
            self.admins.remove(uid)
            self._save_raw(self.admins, ADMINS_DB)
            if str(uid) in self.admin_permissions:
                self.admin_permissions.pop(str(uid), None)
                self._save_raw(self.admin_permissions, ADMIN_PERMISSIONS_DB)
            return True
        return False

    def _load_keyboards(self, filename: str) -> dict:
        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "buttons" in data:
                        return data
            except Exception as e:
                print(f"Error loading keyboards: {e}")
        # Default fallback layout
        defaults = {
            "buttons": [
                [
                    {"text": "🛍️ Cart", "action": "cart"},
                    {"text": "👤 Profile", "action": "profile"},
                    {"text": "ℹ Info", "action": "info"}
                ],
                [
                    {"text": "⚙ Admin Panel", "action": "admin", "admin_only": True}
                ]
            ]
        }
        self._save_raw(defaults, filename)
        return defaults

    def get_custom_keyboards(self) -> dict:
        return self.keyboards

    def save_custom_keyboards(self, keyboards: dict):
        self.keyboards = keyboards
        self._save_raw(keyboards, KEYBOARDS_DB)

    def move_course_order(self, course_id: str, direction: str) -> bool:
        keys = list(self.courses.keys())
        if course_id not in keys:
            return False
        idx = keys.index(course_id)
        if direction == "up":
            if idx == 0:
                return False
            keys[idx], keys[idx-1] = keys[idx-1], keys[idx]
        elif direction == "down":
            if idx == len(keys) - 1:
                return False
            keys[idx], keys[idx+1] = keys[idx+1], keys[idx]
        else:
            return False

        new_courses = {}
        for k in keys:
            new_courses[k] = self.courses[k]
        self.courses = new_courses
        self._save(self.courses, COURSES_DB)
        return True

    def clone_course(self, course_id: str) -> Optional[str]:
        course = self.get_course(course_id)
        if not course:
            return None
        import time
        new_id = f"COURSE-CLONE-{int(time.time())}"
        new_course = dict(course)
        new_course["name"] = f"{course['name']} (Copy)"
        self.courses[new_id] = new_course
        self._save(self.courses, COURSES_DB)
        return new_id

    def toggle_course_status(self, course_id: str) -> Optional[str]:
        course = self.get_course(course_id)
        if not course:
            return None
        current_status = course.get("status", "active")
        new_status = "inactive" if current_status != "inactive" else "active"
        course["status"] = new_status
        self.courses[course_id] = course
        self._save(self.courses, COURSES_DB)
        return new_status

    def _load_settings(self, filename: str) -> dict:
        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading settings: {e}")
        defaults = {
            "maintenance_mode": False,
            "maintenance_message": "🛠️ **StudyMart Bot is currently undergoing scheduled maintenance.**\n\nWe are updating our courses and systems to serve you better. We will be back online shortly! Thank you for your patience."
        }
        self._save_raw(defaults, filename)
        return defaults

    def get_setting(self, key: str, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key: str, value) -> bool:
        self.settings[key] = value
        self._save_raw(self.settings, SETTINGS_DB)
        return True

    def delete_setting(self, key: str) -> bool:
        if key in self.settings:
            del self.settings[key]
            self._save_raw(self.settings, SETTINGS_DB)
            return True
        return False
