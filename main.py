import bcrypt
import jwt
import time

from fastapi import Header
from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
from collections import defaultdict
from bs4 import BeautifulSoup

app = FastAPI()

students = {}
users = {}

SECRET_KEY = "phase4-secret-key"

admin_hash = bcrypt.hashpw(
    b"admin",
    bcrypt.gensalt()
)

users["admin"] = {
    "password": admin_hash,
    "role": "admin"
}
catalog = {}
rate_limits = defaultdict(list)
class HistoryRecord(BaseModel):
    course_code: str
    term: str
    credits_earned: int
    status: str


class HistoryUpdate(BaseModel):
    history: list[HistoryRecord]


class PlannedCourse(BaseModel):
    course_code: str
    term: str


class PlanRequest(BaseModel):
    planned_courses: list[PlannedCourse]


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str
def create_token(username, role):

    payload = {
        "sub": username,
        "role": role,
        "iat": int(time.time())
    }

    return jwt.encode(
        payload,
        SECRET_KEY,
        algorithm="HS256"
    )
def rate_limit(key):

    now = time.time()

    rate_limits[key] = [
        t for t in rate_limits[key]
        if now - t < 60
    ]

    if len(rate_limits[key]) >= 10:
        raise HTTPException(
            status_code=429,
            detail="Too Many Requests"
        )

    rate_limits[key].append(now)

def get_current_user(authorization):

    print("AUTH HEADER =", authorization)

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized"
        )

    try:

        token = authorization.replace(
            "Bearer ",
            ""
        )

        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=["HS256"]
        )

        return payload

    except:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized"
        )
def normalize_course_code(code):
    return code.upper().replace(" ", "").replace("-", "")


def term_sort_key(term):

    seasons = {
        "W": 1,
        "SP": 2,
        "S": 3,
        "F": 4
    }

    try:
        year = int(term[:2])
        season = term[2:]

        return (
            year,
            seasons.get(season, 99)
        )
    except:
        return (999, 999)


def completed_courses(history):

    completed = set()

    for item in history:

        if item["status"] == "Completed":

            completed.add(
                normalize_course_code(
                    item["course_code"]
                )
            )

    return completed
   

@app.post("/api/v1/auth/register", status_code=201)
def register(data: RegisterRequest):

    if data.username in users:

        raise HTTPException(
            status_code=409,
            detail="User already exists"
        )

    password_hash = bcrypt.hashpw(
        data.password.encode(),
        bcrypt.gensalt()
    )

    users[data.username] = {
        "password": password_hash,
        "role": "student"
    }

    students[data.username] = {
        "history": [],
        "plan": []
    }

    return {
        "status": "registered"
    }


@app.post("/api/v1/auth/login")
def login(data: LoginRequest):

    if data.username not in users:

        raise HTTPException(
            status_code=401,
            detail="Invalid credentials"
        )

    user = users[data.username]

    if not bcrypt.checkpw(
        data.password.encode(),
        user["password"]
    ):

        raise HTTPException(
            status_code=401,
            detail="Invalid credentials"
        )

    token = create_token(
        data.username,
        user["role"]
    )

    return {
        "access_token": token,
        "token_type": "bearer"
    }
@app.get("/")
def home():
    return {"message": "API Working"}


@app.post("/api/v1/students/{student_id}/history/import", status_code=201)
async def import_history(
    student_id: str,
    file: UploadFile = File(...),
    authorization: str = Header(None)
):
    user = get_current_user(authorization)

    if user["sub"] != student_id:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized"
        )
    content = await file.read()
    soup = BeautifulSoup(content, "html.parser")

    courses = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])

            if len(cells) < 6:
                continue

            status = cells[0].get_text(strip=True)
            course_code = cells[1].get_text(strip=True)
            term = cells[4].get_text(strip=True)
            credits_text = cells[5].get_text(strip=True)

            if status not in ["Completed", "In-Progress", "Attempted"]:
                continue

            if term == "":
                continue

            try:
                credits = int(float(credits_text))
            except:
                credits = 0

            courses.append({
                "course_code": course_code,
                "term": term,
                "credits_earned": credits,
                "status": status
            })

    dedup = {}

    for course in courses:
        key = (course["course_code"], course["term"])

        if key not in dedup:
            dedup[key] = course
        else:
            if course["credits_earned"] > dedup[key]["credits_earned"]:
                dedup[key] = course

    history = list(dedup.values())

    students[student_id] = {
        "history": history,
        "plan": []
    }

    return {
        "status": "success",
        "past_courses_imported": len(history)
    }


@app.put("/api/v1/students/{student_id}/history")
def update_history(student_id: str, data: HistoryUpdate):

    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")

    students[student_id]["history"] = [
        item.model_dump() for item in data.history
    ]

    return {
        "status": "success",
        "message": "Academic history updated successfully"
    }


@app.delete("/api/v1/students/{student_id}/history")
def delete_history(student_id: str):

    if student_id not in students:
        raise HTTPException(status_code=404, detail="Student not found")

    students[student_id]["history"] = []

    return {"status": "success"}


@app.post("/api/v1/students/{student_id}/plan")
def save_plan(
    student_id: str,
    data: PlanRequest,
    authorization: str = Header(None)
):

    user = get_current_user(authorization)

    if (
        user["sub"] != student_id
        and user["role"] != "admin"
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized"
        )

    if student_id not in students:
        raise HTTPException(
            status_code=404,
            detail="Student not found"
        )

    students[student_id]["plan"] = [
        item.model_dump() for item in data.planned_courses
    ]

    return {
        "status": "success",
        "planned_courses_saved": len(data.planned_courses)
    }


@app.put("/api/v1/students/{student_id}/plan")
def replace_plan(
    student_id: str,
    data: PlanRequest,
    authorization: str = Header(None)
):

    user = get_current_user(authorization)

    if (
        user["sub"] != student_id
        and user["role"] != "admin"
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized"
        )

    if student_id not in students:
        raise HTTPException(
            status_code=404,
            detail="Student not found"
        )

    students[student_id]["plan"] = [
        item.model_dump() for item in data.planned_courses
    ]

    return {
        "status": "success",
        "planned_courses_saved": len(data.planned_courses)
    }

@app.delete("/api/v1/students/{student_id}/plan")
def delete_plan(
    student_id: str,
    authorization: str = Header(None)
):

    user = get_current_user(authorization)

    if (
        user["sub"] != student_id
        and user["role"] != "admin"
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized"
        )

    if student_id not in students:
        raise HTTPException(
            status_code=404,
            detail="Student not found"
        )

    students[student_id]["plan"] = []

    return {
        "status": "success"
    }

@app.post("/api/v1/admin/catalog/import")
async def import_catalog(file: UploadFile = File(...)):

    content = await file.read()
    soup = BeautifulSoup(content, "html.parser")

    tables = soup.find_all("table")

    imported = 0

    for table in tables:

        rows = table.find_all("tr")

        for row in rows[1:]:

            cols = row.find_all("td")

            if len(cols) < 5:
                continue

            course_code = cols[0].get_text(strip=True)

            if course_code == "":
                continue

            title = cols[1].get_text(strip=True)

            try:
                credits = int(cols[2].get_text(strip=True))
            except:
                credits = 0

            prerequisites = cols[3].get_text(strip=True)
            cross_listed = cols[4].get_text(strip=True)

            catalog[normalize_course_code(course_code)] = {
                "course_code": course_code,
                "title": title,
                "credits": credits,
                "prerequisites": prerequisites,
                "cross_listed": cross_listed
            }
            imported += 1

    return {
        "status": "success",
        "courses_imported": imported
    }

@app.get("/api/v1/catalog/courses/{course_code}")
def get_course(course_code: str):

    key = normalize_course_code(course_code)

    if key not in catalog:
        raise HTTPException(
            status_code=404,
            detail="Course not found"
        )

    return catalog[key]


@app.get("/api/v1/students/{student_id}/audit-report")
def audit_report(
    student_id: str,
    strict: bool = False,
    authorization: str = Header(None)
):
    user = get_current_user(authorization)

    rate_limit(user["sub"])
    if student_id not in students:
        raise HTTPException(
            status_code=404,
            detail="Student not found"
        )

    history = students[student_id]["history"]
    plan = students[student_id]["plan"]

    completed = completed_courses(history)

    timeline_validation = []
    cross_list_violations = []

    total_earned = 0

    for item in history:

        if item["status"] == "Completed":
            total_earned += item["credits_earned"]

    total_planned = 0

    for course in plan:

        code = normalize_course_code(
            course["course_code"]
        )

        catalog_course = catalog.get(code)

        if catalog_course:

            total_planned += catalog_course["credits"]

            prereq = catalog_course["prerequisites"]

            if prereq:

                prereq_code = normalize_course_code(prereq)

                if prereq_code not in completed:

                    timeline_validation.append({
                        "term": course["term"],
                        "errors": [
                            {
                                "course_code": course["course_code"],
                                "type": "MISSING_PREREQUISITE",
                                "message": f"Missing prerequisite: {prereq}"
                            }
                        ]
                    })

            cross = catalog_course["cross_listed"]

            if cross:

                cross_code = normalize_course_code(cross)

                if cross_code in completed:

                    cross_list_violations.append({
                        "course_code": course["course_code"],
                        "type": "CROSS_LIST_CONFLICT",
                        "message": f"Cross-listed with completed course {cross}"
                    })

    timeline_validation.sort(
        key=lambda x: term_sort_key(
            x["term"]
        )
    )

    total_remaining = max(
        0,
        120 - total_earned - total_planned
    )

    issues = (
        len(timeline_validation)
        + len(cross_list_violations)
    )

    if issues == 0:
        status = "ok"
    elif strict:
        status = "failed"
    else:
        status = "warning"

    return {
        "student_id": student_id,
        "status": status,
        "timeline_validation": timeline_validation,
        "cross_list_violations": cross_list_violations,
        "credit_summary": {
            "total_earned": total_earned,
            "total_planned": total_planned,
            "total_remaining_for_graduation": total_remaining
        }
    }
@app.get("/api/v1/students/{student_id}/profile")
def get_profile(
    student_id: str,
    authorization: str = Header(None)
):

    user = get_current_user(authorization)

    if (
        user["sub"] != student_id
        and user["role"] != "admin"
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized"
        )

    if student_id not in students:
        raise HTTPException(
            status_code=404,
            detail="Student not found"
        )

    return {
        "student_id": student_id,
        "history": students[student_id]["history"],
        "plan": students[student_id]["plan"]
    }
@app.get("/api/v1/students/{student_id}/recommendations")
def recommendations(
    student_id: str,
    authorization: str = Header(None)
):

    user = get_current_user(authorization)

    if (
        user["sub"] != student_id
        and user["role"] != "admin"
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized"
        )

    if student_id not in students:
        raise HTTPException(
            status_code=404,
            detail="Student not found"
        )

    completed = completed_courses(
        students[student_id]["history"]
    )

    pathway = []

    term_number = 0

    for code, course in catalog.items():

        if code in completed:
            continue

        year = 26 + (term_number // 3)

        seasons = ["F", "W", "S"]

        term = f"{year}{seasons[term_number % 3]}"

        pathway.append({
            "term": term,
            "courses": [course["course_code"]]
        })

        term_number += 1

    return {
        "student_id": student_id,
        "recommended_pathway": pathway
    }