import os
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from cua.fake_app import data
from cua.fake_app.faults import FAULTS

load_dotenv()

FAKE_CREDENTIALS = {"teller1": "teller123"}
MIN_INITIAL_DEPOSIT = 25.00

app = FastAPI(title="Meridian Credit Union - Core Teller System (fake)")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("FAKE_APP_SESSION_SECRET", "dev-only-change-me"),
)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def check_session(request: Request) -> str | RedirectResponse:
    """Returns the logged-in username, or a redirect if the caller can't proceed.

    Also where the `session_expired` fault fires: the cookie is still valid,
    but the server treats the session as dead, mirroring a real backend
    session store expiring independently of the browser's cookie.
    """
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/login", status_code=303)
    if FAULTS.consume("session_expired"):
        request.session.clear()
        return RedirectResponse("/login?expired=1", status_code=303)
    return user


def do_search(q: str) -> dict:
    q = (q or "").strip()
    if not q:
        return {"not_found": True, "query": q, "results": []}

    if q.isdigit():
        member_id = int(q)
        if FAULTS.member_not_found:
            FAULTS.member_not_found = False
            return {"not_found": True, "query": q, "results": []}
        member = data.get_member(member_id)
        if FAULTS.duplicate_members and member:
            FAULTS.duplicate_members = False
            dup_a = dict(member)
            dup_b = dict(member, first_name=member["first_name"] + " (dup.)")
            return {"not_found": False, "query": q, "results": [dup_a, dup_b]}
        if not member:
            return {"not_found": True, "query": q, "results": []}
        return {"not_found": False, "query": q, "results": [member]}

    matches = data.search_by_last_name(q)
    if not matches:
        return {"not_found": True, "query": q, "results": []}
    return {"not_found": False, "query": q, "results": matches}


@app.get("/")
def root(request: Request):
    if request.session.get("user"):
        return RedirectResponse("/app")
    return RedirectResponse("/login")


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, expired: int = 0, error: int = 0):
    return templates.TemplateResponse(
        request, "login.html", {"expired": bool(expired), "error": bool(error)}
    )


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if FAKE_CREDENTIALS.get(username) == password:
        request.session["user"] = username
        return RedirectResponse("/app", status_code=303)
    return RedirectResponse("/login?error=1", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/app", response_class=HTMLResponse)
def app_shell(request: Request):
    user = check_session(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "app_shell.html", {})


@app.get("/app/banner", response_class=HTMLResponse)
def banner(request: Request):
    user = check_session(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "banner.html", {"user": user})


@app.get("/app/nav", response_class=HTMLResponse)
def nav(request: Request):
    user = check_session(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "nav.html", {})


@app.get("/app/main", response_class=HTMLResponse)
def main_frame(request: Request):
    user = check_session(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "main_welcome.html", {})


@app.get("/app/search", response_class=HTMLResponse)
def search(request: Request, q: str = ""):
    user = check_session(request)
    if isinstance(user, RedirectResponse):
        return user
    if FAULTS.consume("slow_load"):
        time.sleep(4)
    ctx = do_search(q)
    return templates.TemplateResponse(request, "search_results.html", ctx)


@app.get("/app/member/{member_id}", response_class=HTMLResponse)
def member_detail(request: Request, member_id: int):
    user = check_session(request)
    if isinstance(user, RedirectResponse):
        return user
    member = data.get_member(member_id)
    if not member:
        return templates.TemplateResponse(request, "not_found.html", {"query": str(member_id)})
    if FAULTS.consume("wrong_member"):
        # Stands in for a real "served the wrong record" bug (a stale cache,
        # a mis-keyed join). Deliberately silent: the page renders normally,
        # just for somebody else.
        other = next(
            (m for m in data.MEMBERS.values() if m["id"] != member_id and m["status"] == "active"), None
        )
        if other is not None:
            member = other
    if member["status"] == "closed":
        return templates.TemplateResponse(
            request, "member_detail.html", {"member": member, "popup": False}
        )
    popup = FAULTS.consume("popup")
    return templates.TemplateResponse(
        request, "member_detail.html", {"member": member, "popup": popup}
    )


@app.get("/app/member/{member_id}/new-subaccount", response_class=HTMLResponse)
def subaccount_form(request: Request, member_id: int):
    user = check_session(request)
    if isinstance(user, RedirectResponse):
        return user
    member = data.get_member(member_id)
    if not member:
        return templates.TemplateResponse(request, "not_found.html", {"query": str(member_id)})
    return templates.TemplateResponse(
        request,
        "subaccount_form.html",
        {"member": member, "user": user, "error": None, "initial_deposit": ""},
    )


@app.post("/app/member/{member_id}/new-subaccount/review", response_class=HTMLResponse)
async def subaccount_review(
    request: Request,
    member_id: int,
    account_type: str = Form(...),
    initial_deposit: str = Form(...),
    opened_by: str = Form(...),
):
    user = check_session(request)
    if isinstance(user, RedirectResponse):
        return user
    member = data.get_member(member_id)
    if not member:
        return templates.TemplateResponse(request, "not_found.html", {"query": str(member_id)})
    try:
        amount = float(initial_deposit.replace(",", "").replace("$", ""))
    except ValueError:
        amount = None
    if amount is None or amount < MIN_INITIAL_DEPOSIT:
        error = f"Initial deposit must be a number of at least ${MIN_INITIAL_DEPOSIT:.2f}."
        return templates.TemplateResponse(
            request,
            "subaccount_form.html",
            {
                "member": member,
                "user": user,
                "error": error,
                "initial_deposit": initial_deposit,
            },
        )
    return templates.TemplateResponse(
        request,
        "subaccount_review.html",
        {
            "member": member,
            "account_type": account_type,
            "initial_deposit": amount,
            "opened_by": opened_by,
        },
    )


@app.post("/app/member/{member_id}/new-subaccount/commit", response_class=HTMLResponse)
async def subaccount_commit(
    request: Request,
    member_id: int,
    account_type: str = Form(...),
    initial_deposit: str = Form(...),
    opened_by: str = Form(...),
):
    user = check_session(request)
    if isinstance(user, RedirectResponse):
        return user
    member = data.get_member(member_id)
    if not member:
        return templates.TemplateResponse(request, "not_found.html", {"query": str(member_id)})
    number = data.next_sub_account_number(member_id)
    member["sub_accounts"].append(
        {"number": number, "account_type": account_type, "initial_deposit": float(initial_deposit), "opened_by": opened_by}
    )
    return templates.TemplateResponse(
        request,
        "subaccount_success.html",
        {"member": member, "sub_account_number": number},
    )


@app.get("/admin/faults", response_class=HTMLResponse)
def admin_faults_page(request: Request):
    return templates.TemplateResponse(request, "admin_faults.html", {"faults": FAULTS.as_dict()})


@app.post("/admin/faults")
async def admin_faults_set(fault: str = Form(...), armed: str = Form(...)):
    FAULTS.set(fault, armed.lower() == "true")
    return RedirectResponse("/admin/faults", status_code=303)


@app.get("/admin/faults/api")
def admin_faults_api_get():
    return JSONResponse(FAULTS.as_dict())


class FaultToggle(BaseModel):
    fault: str
    armed: bool


@app.post("/admin/faults/api")
def admin_faults_api_set(payload: FaultToggle):
    FAULTS.set(payload.fault, payload.armed)
    return JSONResponse(FAULTS.as_dict())


@app.post("/admin/reset")
def admin_reset():
    data.reset()
    FAULTS.reset()
    return JSONResponse({"status": "reset", "faults": FAULTS.as_dict()})
