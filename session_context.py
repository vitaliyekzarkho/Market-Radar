import json
from datetime import (
    datetime,
    time,
    timezone,
)
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


DATA_FILE = (
    Path("data")
    / "raw"
    / "GOLD_pro"
    / "M15.csv"
)

OUTPUT_DIRECTORY = (
    Path("data")
    / "sessions"
)

OUTPUT_FILE = (
    OUTPUT_DIRECTORY
    / "GOLD_pro.json"
)

IRELAND = ZoneInfo("Europe/Dublin")
TOKYO = ZoneInfo("Asia/Tokyo")
LONDON = ZoneInfo("Europe/London")
FRANKFURT = ZoneInfo("Europe/Berlin")
NEW_YORK = ZoneInfo("America/New_York")


SESSION_DEFINITIONS = [
    {
        "name": "Asia",
        "timezone": TOKYO,
        "start": time(9, 0),
        "end": time(17, 0),
        "meaning": (
            "Range and internal liquidity build"
        ),
    },
    {
        "name": "Frankfurt",
        "timezone": FRANKFURT,
        "start": time(8, 0),
        "end": time(17, 30),
        "meaning": (
            "European liquidity activation"
        ),
    },
    {
        "name": "London",
        "timezone": LONDON,
        "start": time(8, 0),
        "end": time(16, 30),
        "meaning": (
            "Asia sweep and directional delivery"
        ),
    },
    {
        "name": "NY Pre-Open",
        "timezone": NEW_YORK,
        "start": time(8, 0),
        "end": time(9, 30),
        "meaning": (
            "US positioning before cash open"
        ),
    },
    {
        "name": "NY Open Window",
        "timezone": NEW_YORK,
        "start": time(9, 30),
        "end": time(10, 30),
        "meaning": (
            "Manipulation, continuation or reversal"
        ),
    },
    {
        "name": "New York",
        "timezone": NEW_YORK,
        "start": time(9, 30),
        "end": time(16, 0),
        "meaning": (
            "US delivery and volume confirmation"
        ),
    },
]


def load_m15():
    data = pd.read_csv(DATA_FILE)

    data["utc_time"] = pd.to_datetime(
        data["utc_time"],
        utc=True,
    )

    for column in [
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
    ]:
        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

    data = data.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    return data.sort_values(
        "utc_time"
    ).reset_index(drop=True)


def local_session_times(
    definition,
    now_utc,
):
    session_timezone = definition["timezone"]

    local_now = now_utc.astimezone(
        session_timezone
    )

    local_date = local_now.date()

    local_start = datetime.combine(
        local_date,
        definition["start"],
        tzinfo=session_timezone,
    )

    local_end = datetime.combine(
        local_date,
        definition["end"],
        tzinfo=session_timezone,
    )

    utc_start = local_start.astimezone(
        timezone.utc
    )

    utc_end = local_end.astimezone(
        timezone.utc
    )

    return {
        "local_now": local_now,
        "local_start": local_start,
        "local_end": local_end,
        "utc_start": utc_start,
        "utc_end": utc_end,
    }


def session_status(
    now_utc,
    utc_start,
    utc_end,
):
    if now_utc < utc_start:
        return "upcoming"

    if now_utc >= utc_end:
        return "completed"

    return "active"


def session_market_data(
    data,
    utc_start,
    utc_end,
    now_utc,
):
    effective_end = min(
        utc_end,
        now_utc,
    )

    session_data = data[
        (
            data["utc_time"] >= utc_start
        )
        &
        (
            data["utc_time"] < effective_end
        )
    ]

    if session_data.empty:
        return None

    median_volume = float(
        session_data[
            "tick_volume"
        ].median()
    )

    return {
        "bars": int(len(session_data)),
        "open": float(
            session_data.iloc[0]["open"]
        ),
        "high": float(
            session_data["high"].max()
        ),
        "low": float(
            session_data["low"].min()
        ),
        "close": float(
            session_data.iloc[-1]["close"]
        ),
        "range": float(
            session_data["high"].max()
            - session_data["low"].min()
        ),
        "median_tick_volume": median_volume,
    }


def build_session(
    definition,
    data,
    now_utc,
):
    times = local_session_times(
        definition,
        now_utc,
    )

    status = session_status(
        now_utc,
        times["utc_start"],
        times["utc_end"],
    )

    market_data = session_market_data(
        data,
        times["utc_start"],
        times["utc_end"],
        now_utc,
    )

    return {
        "name": definition["name"],
        "status": status,
        "meaning": definition["meaning"],
        "timezone": str(
            definition["timezone"]
        ),
        "local_start": (
            times["local_start"].isoformat()
        ),
        "local_end": (
            times["local_end"].isoformat()
        ),
        "utc_start": (
            times["utc_start"].isoformat()
        ),
        "utc_end": (
            times["utc_end"].isoformat()
        ),
        "market_data": market_data,
    }


def find_session(
    sessions,
    name,
):
    for session in sessions:
        if session["name"] == name:
            return session

    return None


def evaluate_sweep(
    data,
    reference_session,
    now_utc,
):
    if (
        reference_session is None
        or reference_session[
            "market_data"
        ] is None
    ):
        return {
            "available": False,
        }

    reference_high = (
        reference_session[
            "market_data"
        ]["high"]
    )

    reference_low = (
        reference_session[
            "market_data"
        ]["low"]
    )

    reference_end = pd.Timestamp(
        reference_session["utc_end"]
    )

    later_data = data[
        (
            data["utc_time"]
            >= reference_end
        )
        &
        (
            data["utc_time"]
            <= now_utc
        )
    ]

    if later_data.empty:
        return {
            "available": True,
            "high_swept": False,
            "low_swept": False,
            "reference_high": reference_high,
            "reference_low": reference_low,
        }

    high_swept = bool(
        (
            later_data["high"]
            > reference_high
        ).any()
    )

    low_swept = bool(
        (
            later_data["low"]
            < reference_low
        ).any()
    )

    return {
        "available": True,
        "high_swept": high_swept,
        "low_swept": low_swept,
        "reference_high": reference_high,
        "reference_low": reference_low,
    }


def next_session_event(
    sessions,
    now_utc,
):
    events = []

    for session in sessions:
        start = datetime.fromisoformat(
            session["utc_start"]
        )

        end = datetime.fromisoformat(
            session["utc_end"]
        )

        if start > now_utc:
            events.append(
                {
                    "time": start,
                    "event": (
                        f"{session['name']} starts"
                    ),
                }
            )

        if end > now_utc:
            events.append(
                {
                    "time": end,
                    "event": (
                        f"{session['name']} ends"
                    ),
                }
            )

    if not events:
        return None

    next_event = min(
        events,
        key=lambda item: item["time"],
    )

    ireland_time = (
        next_event["time"]
        .astimezone(IRELAND)
    )

    return {
        "event": next_event["event"],
        "utc_time": (
            next_event["time"].isoformat()
        ),
        "ireland_time": (
            ireland_time.isoformat()
        ),
        "minutes_until": round(
            (
                next_event["time"]
                - now_utc
            ).total_seconds()
            / 60,
            1,
        ),
    }


def main():
    data = load_m15()
    now_utc = datetime.now(timezone.utc)

    sessions = [
        build_session(
            definition,
            data,
            now_utc,
        )
        for definition in SESSION_DEFINITIONS
    ]

    active_sessions = [
        session["name"]
        for session in sessions
        if session["status"] == "active"
    ]

    asia = find_session(
        sessions,
        "Asia",
    )

    london = find_session(
        sessions,
        "London",
    )

    asia_sweep = evaluate_sweep(
        data,
        asia,
        now_utc,
    )

    london_sweep = evaluate_sweep(
        data,
        london,
        now_utc,
    )

    next_event = next_session_event(
        sessions,
        now_utc,
    )

    result = {
        "symbol": "GOLD.pro",
        "generated_utc": now_utc.isoformat(),
        "ireland_time": (
            now_utc
            .astimezone(IRELAND)
            .isoformat()
        ),
        "active_sessions": active_sessions,
        "sessions": sessions,
        "asia_range_sweep": asia_sweep,
        "london_range_sweep": london_sweep,
        "next_event": next_event,
        "purpose": (
            "Session context only. "
            "Not a standalone directional signal."
        ),
    }

    OUTPUT_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            result,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print(
        "IRELAND_TIME:",
        result["ireland_time"],
    )

    print(
        "ACTIVE_SESSIONS:",
        ", ".join(active_sessions)
        if active_sessions
        else "none",
    )

    for session in sessions:
        market_data = session["market_data"]

        if market_data is None:
            range_text = "no data"
        else:
            range_text = (
                f"{market_data['low']} - "
                f"{market_data['high']}"
            )

        print(
            f"{session['name']}: "
            f"{session['status']} | "
            f"range={range_text}"
        )

    print(
        "ASIA_SWEEP:",
        asia_sweep,
    )

    if next_event:
        print(
            "NEXT_EVENT:",
            next_event["event"],
            "| Ireland:",
            next_event["ireland_time"],
            "| minutes:",
            next_event["minutes_until"],
        )

    print(f"SESSIONS_SAVED: {OUTPUT_FILE}")
    print("SESSION_CONTEXT: PASSED")


if __name__ == "__main__":
    main()