import json
import os

COURSES_FILE = "courses.json"

def load_courses():
    if os.path.exists(COURSES_FILE):
        try:
            with open(COURSES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading {COURSES_FILE}: {e}")
    return {}

def save_courses(data):
    try:
        with open(COURSES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("✅ কোর্স ডেটাবেস সফলভাবে আপডেট হয়েছে!")
    except Exception as e:
        print(f"Error saving {COURSES_FILE}: {e}")

def add_course():
    courses = load_courses()
    
    print("\n--- ➕ নতুন কোর্স যোগ করুন ---")
    name = input("📖 কোর্সের নাম (যেমন: ACS FRB Science | SSC'27): ").strip()
    try:
        price = int(input("💰 মূল্য (টাকায়, যেমন: 200, ফ্রি হলে 0): ").strip())
    except ValueError:
        price = 0
    category = input("📂 ক্যাটাগরি (SSC / HSC 28 / HSC 27 / HSC 26 ইত্যাদি): ").strip().upper()
    program = input("🎯 প্রোগ্রাম (academy / revision / admission): ").strip().lower()
    image = input("🖼️ কোর্সের ব্যানার ছবির লিংক (Web Image URL বা ফাঁকা রাখতে Enter চাপুন): ").strip()
    instructor = input("👨‍🏫 Teacher Panel / ইন্সট্রাক্টর (যেমন: DU • BUET • RUET-এর অভিজ্ঞ শিক্ষকবৃন্দ): ").strip()
    access_link = input("🔗 কোর্সের অ্যাক্সেস লিংক (Telegram Channel / Drive Link): ").strip()
    description = input("📝 কোর্সের বিবরণ (যেমন: পরীক্ষার আগে অল্প সময়ে পুরো সিলেবাস রিভিশন): ").strip()
    features = input("✨ কোর্সে যা যা থাকছে (যেমন: 🔹 Topic-wise HD Revision Class \\n 🔹 Lecture Sheet): ").strip()
    
    course_id = f"COURSE-{len(courses)+1}"
    
    courses[course_id] = {
        "id": course_id,
        "name": name,
        "price": price,
        "category": category,
        "program": program,
        "image": image,
        "description": description,
        "instructor": instructor,
        "features": features.replace("\\n", "\n"),
        "access_link": access_link
    }
    
    save_courses(courses)
    print(f"\n🎉 '{name}' কোর্সটি সফলভাবে যোগ করা হয়েছে! (ID: {course_id})")

def list_courses():
    courses = load_courses()
    if not courses:
        print("\n❌ কোনো কোর্স পাওয়া যায়নি!")
        return
    
    print("\n--- 📋 সকল কোর্সের তালিকা ---")
    for cid, c in courses.items():
        price_tag = f"{c['price']} ৳" if c.get('price', 0) > 0 else "Free 🎁"
        print(f"🆔 [{cid}] {c['name']} - {price_tag} ({c.get('category')}/{c.get('program')})")
        if c.get('image'):
            print(f"   🖼️ Image: {c.get('image')[:40]}...")
        if c.get('access_link'):
            print(f"   🔗 Link: {c.get('access_link')}")

def delete_course():
    courses = load_courses()
    list_courses()
    
    cid = input("\n🗑️ যে কোর্সটি ডিলিট করতে চান তার ID লিখুন: ").strip()
    if cid in courses:
        name = courses[cid]["name"]
        del courses[cid]
        save_courses(courses)
        print(f"✅ '{name}' কোর্সটি মুছে ফেলা হয়েছে!")
    else:
        print("❌ কোর্স আইডি খুঁজে পাওয়া যায়নি!")

def main():
    while True:
        print("\n==================================")
        print("   🎓 StudyMart Course Manager   ")
        print("==================================")
        print("1. ➕ নতুন কোর্স যোগ করুন (Add Course)")
        print("2. 📋 কোর্স তালিকা দেখুন (List Courses)")
        print("3. 🗑️ কোর্স ডিলিট করুন (Delete Course)")
        print("4. 🚪 প্রস্থান (Exit)")
        
        choice = input("\nআপনার পছন্দ নির্বাচন করুন (1-4): ").strip()
        
        if choice == "1":
            add_course()
        elif choice == "2":
            list_courses()
        elif choice == "3":
            delete_course()
        elif choice == "4":
            print("\nধন্যবাদ! বিদায়!")
            break
        else:
            print("⚠️ অনুগ্রহ করে ১ থেকে ৪ এর মধ্যে নির্বাচন করুন।")

if __name__ == "__main__":
    main()
