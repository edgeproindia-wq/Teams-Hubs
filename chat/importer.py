"""Excel import of employees (contacts) for a company admin."""
import io
import secrets

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from openpyxl import Workbook, load_workbook

from .models import User

MAX_ROWS = 100            # password hashing is slow on purpose; keep one import quick
MAX_BYTES = 2 * 1024 * 1024
HEADERS = {
    "name": {"name", "full name", "employee name", "employee"},
    "email": {"email", "email id", "work email", "e-mail", "mail", "email address"},
    "job_title": {"job title", "title", "designation", "role"},
    "department": {"department", "dept", "team"},
}
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"


class ImportProblem(Exception):
    """A problem with the whole file (shown to the admin as one message)."""


def temp_password():
    return "".join(secrets.choice(_ALPHABET) for _ in range(10))


def read_rows(fileobj):
    try:
        wb = load_workbook(fileobj, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows_iter = ws.iter_rows(values_only=True)
        header = next(rows_iter, None)
        if header is None:
            raise ImportProblem("The first sheet is empty.")
        cols = {}
        for idx, h in enumerate(header):
            key = str(h or "").strip().lower()
            for field, aliases in HEADERS.items():
                if key in aliases and field not in cols:
                    cols[field] = idx
        if "name" not in cols or "email" not in cols:
            raise ImportProblem("Row 1 must have the headings Name and Email "
                                "(optional: Job Title, Department). Download the template to see the format.")
        rows = []
        for n, r in enumerate(rows_iter, start=2):
            vals = {f: (str(r[i]).strip() if i < len(r) and r[i] is not None else "") for f, i in cols.items()}
            if not any(vals.values()):
                continue
            vals["row"] = n
            rows.append(vals)
            if len(rows) > MAX_ROWS:
                raise ImportProblem(f"Too many rows. Import at most {MAX_ROWS} employees per file; split the file.")
        if not rows:
            raise ImportProblem("No employee rows found under the headings.")
        return rows
    except ImportProblem:
        raise
    except Exception:
        raise ImportProblem("Could not read this file. Upload an Excel .xlsx file.") from None


def import_employees(company, rows):
    """Create accounts. Returns (created, skipped); created rows carry the temporary password."""
    created, skipped, seen = [], [], set()
    for r in rows:
        name = " ".join(r.get("name", "").split())
        email = r.get("email", "").strip().lower()
        who = {"row": r["row"], "name": name, "email": email}
        if not name or len(name) > 150:
            skipped.append({**who, "reason": "Name is missing or too long"}); continue
        try:
            validate_email(email)
            if len(email) > 150:
                raise ValidationError("long")
        except ValidationError:
            skipped.append({**who, "reason": "Email is not valid"}); continue
        if email in seen:
            skipped.append({**who, "reason": "Duplicate email in this file"}); continue
        seen.add(email)
        if User.objects.filter(username__iexact=email).exists():
            skipped.append({**who, "reason": "This email already has an account"}); continue
        first, _, last = name.partition(" ")
        pw = temp_password()
        user = User(username=email, email=email, first_name=first[:150], last_name=last[:150], company=company,
                    role=User.EMPLOYEE, job_title=r.get("job_title", "")[:100],
                    department=r.get("department", "")[:100], must_change_password=True)
        user.set_password(pw)
        user.save()
        created.append({**who, "job_title": user.job_title, "department": user.department, "password": pw})
    return created, skipped


def _put(ws, row, col, value):
    cell = ws.cell(row=row, column=col, value=value)
    if isinstance(value, str):
        cell.data_type = "s"   # text only - never let Excel treat imported text as a formula
    return cell


def credentials_xlsx(created):
    wb = Workbook()
    ws = wb.active
    ws.title = "New accounts"
    for c, h in enumerate(["Name", "Email", "Temporary password", "Job title", "Department"], 1):
        ws.cell(row=1, column=c, value=h)
    for r, u in enumerate(created, 2):
        for c, k in enumerate(["name", "email", "password", "job_title", "department"], 1):
            _put(ws, r, c, u[k])
    for col, w in zip("ABCDE", (28, 34, 20, 24, 20)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def template_xlsx():
    wb = Workbook()
    ws = wb.active
    ws.title = "Employees"
    for c, h in enumerate(["Name", "Email", "Job Title", "Department"], 1):
        ws.cell(row=1, column=c, value=h)
    for col, w in zip("ABCD", (28, 34, 24, 20)):
        ws.column_dimensions[col].width = w
    ex = wb.create_sheet("Example")
    ex.append(["Name", "Email", "Job Title", "Department"])
    ex.append(["Priya Sharma", "priya@yourcompany.com", "Senior Detailer", "Detailing"])
    ex.append(["Fill in the 'Employees' sheet only. Only the first sheet is imported."])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
