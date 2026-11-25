import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).parent / "clinic.db"

SEED_DOCTORS = [
    ("doc-rao", "Dr. Ananya Rao", "Cardiology", "Suite 204", ["09:30", "10:30", "14:00", "16:00"]),
    ("doc-mehta", "Dr. Vikram Mehta", "Dermatology", "Suite 108", ["10:00", "11:30", "15:00", "17:00"]),
    ("doc-nair", "Dr. Priya Nair", "Pediatrics", "Suite 302", ["09:00", "11:00", "13:30", "15:30"]),
    ("doc-kulkarni", "Dr. Rohan Kulkarni", "Orthopedics", "Suite 215", ["10:30", "12:00", "14:30", "16:30"]),
]


def normalize_phone(raw: str) -> str:
    """Keep digits only so spoken phone numbers match reliably."""
    digits = re.sub(r"\D", "", raw or "")
    # Strip leading country code '91' or '1' if 11-12 digits for consistent local matching
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def format_phone_display(digits: str) -> str:
    if len(digits) == 10:
        return f"{digits[:3]} {digits[3:6]} {digits[6:]}"
    return digits


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(force_reset: bool = False) -> None:
    if force_reset and DB_PATH.exists():
        DB_PATH.unlink()

    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS doctors (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                specialty TEXT NOT NULL,
                room TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS slots (
                id TEXT PRIMARY KEY,
                doctor_id TEXT NOT NULL REFERENCES doctors(id),
                slot_date TEXT NOT NULL,
                slot_time TEXT NOT NULL,
                is_booked INTEGER NOT NULL DEFAULT 0,
                UNIQUE(doctor_id, slot_date, slot_time)
            );

            CREATE TABLE IF NOT EXISTS appointments (
                id TEXT PRIMARY KEY,
                doctor_id TEXT NOT NULL REFERENCES doctors(id),
                slot_id TEXT NOT NULL REFERENCES slots(id),
                slot_date TEXT NOT NULL,
                slot_time TEXT NOT NULL,
                caller_phone TEXT NOT NULL,
                caller_name TEXT,
                status TEXT NOT NULL DEFAULT 'confirmed',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )

        existing = conn.execute("SELECT COUNT(*) FROM doctors").fetchone()[0]
        if existing > 0:
            return

        today = date.today()
        dates = [today + timedelta(days=offset) for offset in range(3)]

        for doc_id, name, specialty, room, times in SEED_DOCTORS:
            conn.execute(
                "INSERT INTO doctors (id, name, specialty, room) VALUES (?, ?, ?, ?)",
                (doc_id, name, specialty, room),
            )
            for d in dates:
                d_str = d.isoformat()
                for t in times:
                    slot_id = f"{doc_id}_{d_str}_{t.replace(':', '')}"
                    conn.execute(
                        "INSERT INTO slots (id, doctor_id, slot_date, slot_time, is_booked) VALUES (?, ?, ?, ?, 0)",
                        (slot_id, doc_id, d_str, t),
                    )

        # Seed two initial appointments so the ledger is realistic on first load
        # (and reviewers can immediately test cancellation with 9876543210).
        seed_bookings = [
            ("APT-101", "doc-rao", dates[0].isoformat(), "10:30", "9876543210", "Aarav Sharma"),
            ("APT-102", "doc-mehta", dates[1].isoformat(), "11:30", "9123456780", "Meera Iyer"),
        ]
        for apt_id, doc_id, d_str, t, phone, caller_name in seed_bookings:
            slot_id = f"{doc_id}_{d_str}_{t.replace(':', '')}"
            conn.execute("UPDATE slots SET is_booked = 1 WHERE id = ?", (slot_id,))
            conn.execute(
                """
                INSERT INTO appointments (id, doctor_id, slot_id, slot_date, slot_time, caller_phone, caller_name, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'confirmed')
                """,
                (apt_id, doc_id, slot_id, d_str, t, phone, caller_name),
            )


def list_doctors_with_slots() -> list[dict[str, Any]]:
    with get_conn() as conn:
        docs = [dict(r) for r in conn.execute("SELECT * FROM doctors ORDER BY name").fetchall()]
        for doc in docs:
            rows = conn.execute(
                """
                SELECT id, slot_date, slot_time, is_booked
                FROM slots
                WHERE doctor_id = ?
                ORDER BY slot_date, slot_time
                """,
                (doc["id"],),
            ).fetchall()
            doc["slots"] = [
                {
                    "id": r["id"],
                    "date": r["slot_date"],
                    "time": r["slot_time"],
                    "is_booked": bool(r["is_booked"]),
                }
                for r in rows
            ]
        return docs


def list_appointments() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.doctor_id, d.name AS doctor_name, d.specialty, d.room,
                   a.slot_id, a.slot_date, a.slot_time, a.caller_phone, a.caller_name,
                   a.status, a.created_at
            FROM appointments a
            JOIN doctors d ON d.id = a.doctor_id
            ORDER BY a.created_at DESC, a.slot_date ASC
            """
        ).fetchall()
        return [
            {
                **dict(r),
                "caller_phone_display": format_phone_display(r["caller_phone"]),
            }
            for r in rows
        ]


def _resolve_doctor(conn: sqlite3.Connection, doctor_name: str | None, specialty: str | None):
    docs = conn.execute("SELECT * FROM doctors").fetchall()
    if doctor_name:
        needle = doctor_name.lower().replace("dr.", "").replace("doctor", "").strip()
        for d in docs:
            if needle and needle in d["name"].lower():
                return d
    if specialty:
        needle = specialty.lower().strip()
        for d in docs:
            if needle and needle in d["specialty"].lower():
                return d
    return None


def _normalize_date(raw_date: str | None) -> str | None:
    if not raw_date:
        return None
    cleaned = raw_date.strip().lower()
    today = date.today()
    if cleaned in ("today",):
        return today.isoformat()
    if cleaned in ("tomorrow",):
        return (today + timedelta(days=1)).isoformat()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", cleaned):
        return cleaned
    return None


def _normalize_time(raw_time: str) -> str:
    """Convert spoken/12h times like '2:00 PM' or '14:00' into HH:MM 24-hour format."""
    t = raw_time.strip().lower().replace(".", "")
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", t)
    if not m:
        return raw_time.strip()
    hour = int(m.group(1))
    minute = int(m.group(2) or "0")
    meridiem = m.group(3)
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def check_doctor_availability(
    doctor_name: str | None = None,
    specialty: str | None = None,
    target_date: str | None = None,
) -> dict[str, Any]:
    norm_date = _normalize_date(target_date)
    with get_conn() as conn:
        query = """
            SELECT d.id AS doctor_id, d.name, d.specialty, d.room, s.slot_date, s.slot_time
            FROM slots s
            JOIN doctors d ON d.id = s.doctor_id
            WHERE s.is_booked = 0
        """
        params: list[Any] = []

        if doctor_name:
            needle = doctor_name.lower().replace("dr.", "").replace("doctor", "").strip()
            query += " AND LOWER(d.name) LIKE ?"
            params.append(f"%{needle}%")

        if specialty:
            query += " AND LOWER(d.specialty) LIKE ?"
            params.append(f"%{specialty.lower().strip()}%")

        if norm_date:
            query += " AND s.slot_date = ?"
            params.append(norm_date)

        query += " ORDER BY d.name, s.slot_date, s.slot_time"
        rows = conn.execute(query, params).fetchall()

        grouped: dict[str, dict[str, Any]] = {}
        for r in rows:
            doc = grouped.setdefault(
                r["doctor_id"],
                {
                    "doctor": r["name"],
                    "specialty": r["specialty"],
                    "room": r["room"],
                    "available_slots": {},
                },
            )
            doc["available_slots"].setdefault(r["slot_date"], []).append(r["slot_time"])

        return {
            "today": date.today().isoformat(),
            "matches": list(grouped.values()),
            "total_open_slots": len(rows),
        }


def book_appointment(
    caller_phone: str,
    doctor_name: str,
    target_date: str,
    time_slot: str,
    caller_name: str | None = None,
) -> dict[str, Any]:
    phone = normalize_phone(caller_phone)
    if len(phone) < 7:
        return {"ok": False, "error": "Invalid phone number. Please confirm all digits with the caller."}

    norm_date = _normalize_date(target_date)
    if not norm_date:
        return {"ok": False, "error": f"Could not parse date '{target_date}'. Use YYYY-MM-DD, today, or tomorrow."}

    norm_time = _normalize_time(time_slot)

    with get_conn() as conn:
        doc = _resolve_doctor(conn, doctor_name, None)
        if not doc:
            return {"ok": False, "error": f"No doctor found matching '{doctor_name}'."}

        slot = conn.execute(
            """
            SELECT * FROM slots
            WHERE doctor_id = ? AND slot_date = ? AND slot_time = ?
            """,
            (doc["id"], norm_date, norm_time),
        ).fetchone()

        if not slot:
            return {
                "ok": False,
                "error": f"{doc['name']} does not have a {norm_time} slot on {norm_date}.",
            }
        if slot["is_booked"]:
            return {
                "ok": False,
                "error": f"That {norm_time} slot on {norm_date} with {doc['name']} is already booked.",
            }

        apt_id = f"APT-{uuid.uuid4().hex[:4].upper()}"
        conn.execute("UPDATE slots SET is_booked = 1 WHERE id = ?", (slot["id"],))
        conn.execute(
            """
            INSERT INTO appointments (id, doctor_id, slot_id, slot_date, slot_time, caller_phone, caller_name, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'confirmed')
            """,
            (apt_id, doc["id"], slot["id"], norm_date, norm_time, phone, caller_name or None),
        )

        return {
            "ok": True,
            "appointment": {
                "id": apt_id,
                "doctor_id": doc["id"],
                "doctor_name": doc["name"],
                "specialty": doc["specialty"],
                "room": doc["room"],
                "slot_id": slot["id"],
                "slot_date": norm_date,
                "slot_time": norm_time,
                "caller_phone": phone,
                "caller_phone_display": format_phone_display(phone),
                "caller_name": caller_name or None,
                "status": "confirmed",
            },
        }


def cancel_appointment(caller_phone: str, appointment_id: str | None = None) -> dict[str, Any]:
    phone = normalize_phone(caller_phone)
    if len(phone) < 7:
        return {"ok": False, "error": "Invalid phone number. Ask the caller to repeat their number."}

    with get_conn() as conn:
        if appointment_id:
            row = conn.execute(
                """
                SELECT a.*, d.name AS doctor_name, d.specialty
                FROM appointments a
                JOIN doctors d ON d.id = a.doctor_id
                WHERE a.id = ? AND a.caller_phone = ? AND a.status = 'confirmed'
                """,
                (appointment_id.upper(), phone),
            ).fetchone()
        else:
            rows = conn.execute(
                """
                SELECT a.*, d.name AS doctor_name, d.specialty
                FROM appointments a
                JOIN doctors d ON d.id = a.doctor_id
                WHERE a.caller_phone = ? AND a.status = 'confirmed'
                ORDER BY a.slot_date ASC, a.slot_time ASC
                """,
                (phone,),
            ).fetchall()
            if len(rows) > 1:
                return {
                    "ok": False,
                    "needs_clarification": True,
                    "message": "Multiple active appointments found for this phone number. Ask the caller which one to cancel.",
                    "appointments": [
                        {
                            "id": r["id"],
                            "doctor_name": r["doctor_name"],
                            "slot_date": r["slot_date"],
                            "slot_time": r["slot_time"],
                        }
                        for r in rows
                    ],
                }
            row = rows[0] if rows else None

        if not row:
            return {
                "ok": False,
                "error": f"No active appointment found under phone number {format_phone_display(phone)}.",
            }

        conn.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (row["id"],))
        conn.execute("UPDATE slots SET is_booked = 0 WHERE id = ?", (row["slot_id"],))

        return {
            "ok": True,
            "cancelled": {
                "id": row["id"],
                "doctor_id": row["doctor_id"],
                "doctor_name": row["doctor_name"],
                "specialty": row["specialty"],
                "slot_id": row["slot_id"],
                "slot_date": row["slot_date"],
                "slot_time": row["slot_time"],
                "caller_phone": phone,
                "caller_phone_display": format_phone_display(phone),
                "status": "cancelled",
            },
        }