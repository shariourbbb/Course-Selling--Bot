import json

sample_courses = {
    "COURSE-1": {
        "id": "COURSE-1",
        "name": "ACS",
        "price": 0,
        "category": "HSC 28",
        "program": "academy",
        "description": "ACS complete course materials.",
        "instructor": "StudyMart Team",
        "features": "Lifetime access\nHD Resolution class\nClass notes"
    },
    "COURSE-2": {
        "id": "COURSE-2",
        "name": "Bondi Pathshala",
        "price": 0,
        "category": "HSC 28",
        "program": "academy",
        "description": "Bondi Pathshala complete course.",
        "instructor": "StudyMart Team",
        "features": "Lifetime access\nHD Resolution class"
    },
    "COURSE-3": {
        "id": "COURSE-3",
        "name": "Redwan's Method",
        "price": 0,
        "category": "HSC 28",
        "program": "academy",
        "description": "Redwan's Method complete course.",
        "instructor": "Redwan Vaia",
        "features": "Lifetime access\nHD Resolution class"
    },
    "COURSE-4": {
        "id": "COURSE-4",
        "name": "FT EBI 4.0",
        "price": 150,
        "category": "HSC 28",
        "program": "academy",
        "description": "FT EBI 4.0 complete course.",
        "instructor": "FT Team",
        "features": "Lifetime access\nHD Resolution class\nPractice sheets"
    },
    "COURSE-5": {
        "id": "COURSE-5",
        "name": "EBI By TMS 6.0",
        "price": 150,
        "category": "HSC 28",
        "program": "academy",
        "description": "EBI By TMS 6.0 complete course.",
        "instructor": "TMS Team",
        "features": "Lifetime access\nHD Resolution class"
    },
    "COURSE-6": {
        "id": "COURSE-6",
        "name": "ICT DECODER | HSC Crackers",
        "price": 120,
        "category": "HSC 28",
        "program": "academy",
        "description": "ICT DECODER complete course for HSC.",
        "instructor": "HSC Crackers Team",
        "features": "Lifetime access\nHD Resolution class\nDoubt solving"
    },
    "COURSE-7": {
        "id": "COURSE-7",
        "name": "BH Biology Full Course | HSC 28",
        "price": 400,
        "category": "HSC 28",
        "program": "academy",
        "description": "BH Biology complete course for HSC 28.",
        "instructor": "BH Biology Team",
        "features": "Lifetime access\nHD Resolution class\nClass notes\nPractice sheets"
    }
}

with open("courses.json", "w", encoding="utf-8") as f:
    json.dump(sample_courses, f, ensure_ascii=False, indent=2)

print("✅ Sample courses created!")
