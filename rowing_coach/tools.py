"""Custom Function Tools for Rowing Coach ADK Agent.

Implements Concept2 physics, Sam Dutney training system calculations,
athlete data access, and weekly sheet generation.
"""

from __future__ import annotations

import json
import math
import os
import pathlib
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
ATHLETES_FILE = DATA_DIR / "athletes.json"
PLANS_DIR = PROJECT_ROOT / "plans"


# ==============================================================================
# Concept2 Physics & Mathematical Engine
# Formulas extracted directly from CASC Squad sheet & Concept2 physics
# ==============================================================================

def pace_to_seconds(pace_str: str) -> float:
    """Converts a split string like '2:10.0' or '2:10' or '1:54.9' into seconds."""
    pace_str = str(pace_str).strip()
    if not pace_str or pace_str == "N/A":
        return 0.0
    parts = pace_str.split(":")
    if len(parts) == 2:
        minutes = float(parts[0])
        seconds = float(parts[1])
        return minutes * 60.0 + seconds
    try:
        return float(pace_str)
    except ValueError:
        return 0.0


def seconds_to_pace(seconds: float, include_tenths: bool = True) -> str:
    """Converts seconds into 'M:SS.T' or 'M:SS' format."""
    if seconds <= 0:
        return "N/A"
    minutes = int(seconds // 60)
    rem_seconds = seconds % 60
    if include_tenths:
        return f"{minutes}:{rem_seconds:04.1f}"
    return f"{minutes}:{round(rem_seconds):02d}"


def split_to_watts(split_sec: float) -> float:
    """Calculates Concept2 mechanical power in Watts from 500m split time in seconds.
    
    Formula: Watts = 2.80 / (split_sec / 500)^3
    """
    if split_sec <= 0:
        return 0.0
    return 2.80 / ((split_sec / 500.0) ** 3)


def watts_to_split(watts: float) -> float:
    """Calculates Concept2 500m split time in seconds from mechanical Watts.
    
    Formula: split_sec = 500 * (2.80 / Watts)^(1/3)
    """
    if watts <= 0:
        return 0.0
    return 500.0 * ((2.80 / watts) ** (1.0 / 3.0))


def calculate_percent_split(split_2k_sec: float, percent_watts: float) -> float:
    """Calculates target split in seconds for a given percentage of 2k Watts.
    
    Formula: split_target = split_2k * (percent_watts)^(-1/3)
    """
    if split_2k_sec <= 0 or percent_watts <= 0:
        return 0.0
    return split_2k_sec * (percent_watts ** (-1.0 / 3.0))


def calculate_recovery_split(split_2k_sec: float) -> float:
    """Calculates Recovery split (14-16 SPM) from 2k split.
    
    Formula from CASC Squad Sheet: Split_recup = Split_2k * 1.41 (~36% 2kW)
    """
    if split_2k_sec <= 0:
        return 0.0
    return split_2k_sec * 1.41


def calculate_l4_split(split_2k_sec: float, stroke_rate: int) -> float:
    """Calculates Wolverine Plan Level 4 split at a given stroke rate.
    
    Exact Formula from CASC Squad Sheet:
    Split_L4(R) = Split_2k * ((152 - 2 * R) / 96)
    
    For R=16: ratio = 120/96 = 1.2500
    For R=17: ratio = 118/96 = 1.2292
    For R=18: ratio = 116/96 = 1.2083
    For R=20: ratio = 112/96 = 1.1667
    For R=22: ratio = 108/96 = 1.1250
    For R=28: ratio = 96/96  = 1.0000 (100% 2k pace)
    """
    if split_2k_sec <= 0 or stroke_rate <= 0:
        return 0.0
    ratio = (152.0 - 2.0 * float(stroke_rate)) / 96.0
    return split_2k_sec * ratio


def get_closest_l4_rate(
    split_2k_sec: float, target_split_sec: float, min_rate: int = 16, max_rate: int = 28
) -> Tuple[int, float]:
    """Finds the L4 stroke rate (odd or even) whose split is closest to target_split_sec.
    
    Returns:
        (best_stroke_rate, best_l4_split_sec)
    """
    if split_2k_sec <= 0 or target_split_sec <= 0:
        return (20, 0.0)
    best_rate = min_rate
    best_diff = float("inf")
    best_split = 0.0

    for r in range(min_rate, max_rate + 1):
        s = calculate_l4_split(split_2k_sec, r)
        diff = abs(s - target_split_sec)
        if diff < best_diff:
            best_diff = diff
            best_rate = r
            best_split = s

    return (best_rate, best_split)


# ==============================================================================
# Athlete Data Access
# ==============================================================================

def _load_athletes() -> Dict[str, Any]:
    """Loads athletes database from data/athletes.json and returns a dict mapping id -> athlete."""
    if not ATHLETES_FILE.exists():
        raise FileNotFoundError(f"Athletes file not found: {ATHLETES_FILE}")
    with open(ATHLETES_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    
    raw_list = raw.get("athletes", [])
    athletes_dict = {}
    for a in raw_list:
        aid = a.get("id")
        if not aid:
            continue
        entry = dict(a)
        # Normalize benchmark fields
        bench = a.get("current_benchmark", {})
        entry["benchmark_2k_split"] = bench.get("2k_reference_split", "")
        entry["benchmark_2k_watts"] = bench.get("2k_reference_watts", 0.0)
        
        # Normalize drag factor
        df = a.get("drag_factor_range")
        if isinstance(df, list) and len(df) == 2:
            entry["drag_factor"] = f"{df[0]}–{df[1]}"
        elif df is not None:
            entry["drag_factor"] = str(df)
        else:
            entry["drag_factor"] = "120–123"
            
        athletes_dict[aid] = entry

    return athletes_dict


def lookup_athlete(query: str) -> Dict[str, Any]:
    """Looks up an athlete's profile and active benchmarks by name or ID.
    
    Args:
        query: Full name, first name, last name, or athlete ID (e.g. 'alex', 'sophie', 'david', 'alex_martin').
        
    Returns:
        Dictionary containing athlete's benchmark 2k split, 2k watts, HR max, Zone 1 cap,
        drag factor, diagnostic test results, and notes.
    """
    athletes = _load_athletes()
    query_lower = query.strip().lower()

    # Exact match on ID
    if query_lower in athletes:
        a = athletes[query_lower]
        a["id"] = query_lower
        return {"status": "success", "athlete": a}

    # Search in name or ID
    matches = []
    for aid, a in athletes.items():
        name = a.get("name", "").lower()
        if query_lower in aid.lower() or query_lower in name:
            athlete_copy = dict(a)
            athlete_copy["id"] = aid
            matches.append(athlete_copy)

    if len(matches) == 1:
        return {"status": "success", "athlete": matches[0]}
    elif len(matches) > 1:
        names = [f"{m['name']} (id: {m['id']})" for m in matches]
        return {
            "status": "multiple_matches",
            "message": f"Found {len(matches)} athletes matching '{query}': {', '.join(names)}. Please refine your query.",
            "matches": matches,
        }
    return {
        "status": "not_found",
        "message": f"No athlete found matching '{query}'. Available athletes: {', '.join(a.get('name', k) for k, a in athletes.items())}",
    }


def calculate_athlete_pacing(athlete_id: str) -> Dict[str, Any]:
    """Calculates the complete Sam Dutney & Wolverine pacing profile for an athlete.
    
    Args:
        athlete_id: Athlete key from athletes.json (e.g. 'alex_martin', 'sophie_dubois', 'david_montgamery').
        
    Returns:
        Dictionary with full calculated splits: 2k reference, AN power, recovery,
        UT3, UT2, UT1, AT, TR, Warmup A/B paces, and L4 rates 16 to 28.
    """
    res = lookup_athlete(athlete_id)
    if res.get("status") != "success":
        return res
    
    athlete = res["athlete"]
    split_2k_sec = pace_to_seconds(athlete["benchmark_2k_split"])
    watts_2k = athlete.get("benchmark_2k_watts") or round(split_to_watts(split_2k_sec), 1)

    # Core zones
    p_recup = calculate_recovery_split(split_2k_sec)
    p_an_145 = calculate_percent_split(split_2k_sec, 1.45)
    p_an_155 = calculate_percent_split(split_2k_sec, 1.55)
    p_ut3_40 = calculate_percent_split(split_2k_sec, 0.40)
    p_ut3_45 = calculate_percent_split(split_2k_sec, 0.45)
    p_ut2_50 = calculate_percent_split(split_2k_sec, 0.50)
    p_ut2_55 = calculate_percent_split(split_2k_sec, 0.55)
    p_ut2_60 = calculate_percent_split(split_2k_sec, 0.60)
    p_ut1_65 = calculate_percent_split(split_2k_sec, 0.65)
    p_ut1_70 = calculate_percent_split(split_2k_sec, 0.70)
    p_30r20_725 = calculate_percent_split(split_2k_sec, 0.725)
    p_at_80 = calculate_percent_split(split_2k_sec, 0.80)
    p_tr_90 = calculate_percent_split(split_2k_sec, 0.90)
    p_tr_105 = calculate_percent_split(split_2k_sec, 1.05)

    # Warmup paces
    warmup_paces = {
        "35%": seconds_to_pace(calculate_percent_split(split_2k_sec, 0.35)),
        "40%": seconds_to_pace(p_ut3_40),
        "45%": seconds_to_pace(p_ut3_45),
        "50%": seconds_to_pace(p_ut2_50),
        "60%": seconds_to_pace(p_ut2_60),
        "70%": seconds_to_pace(p_ut1_70),
        "80%": seconds_to_pace(p_at_80),
        "90%": seconds_to_pace(p_tr_90),
        "105%": seconds_to_pace(p_tr_105),
        "145%": seconds_to_pace(p_an_145),
        "155%": seconds_to_pace(p_an_155),
    }

    # Full L4 table (rates 16 to 28)
    l4_table = {}
    for r in range(16, 29):
        l4_table[f"@{r}"] = seconds_to_pace(calculate_l4_split(split_2k_sec, r))

    return {
        "status": "success",
        "athlete_id": athlete["id"],
        "name": athlete["name"],
        "category": athlete.get("category", ""),
        "benchmark_2k_split": athlete["benchmark_2k_split"],
        "benchmark_2k_watts": watts_2k,
        "hr_max": athlete.get("hr_max"),
        "hr_zone1_cap": athlete.get("hr_zone1_cap"),
        "drag_factor": athlete.get("drag_factor"),
        "zones": {
            "recovery_14_16": seconds_to_pace(p_recup),
            "an_power_range": f"{seconds_to_pace(p_an_155)} - {seconds_to_pace(p_an_145)}",
            "ut3_45pct": seconds_to_pace(p_ut3_45),
            "ut3_range": f"{seconds_to_pace(p_ut2_50)} - {seconds_to_pace(p_ut3_40)}",
            "ut2_range": f"{seconds_to_pace(p_ut2_60)} - {seconds_to_pace(p_ut2_50)}",
            "ut2_midpoint_55pct": seconds_to_pace(p_ut2_55),
            "ut1_range": f"{seconds_to_pace(p_ut1_70)} - {seconds_to_pace(p_ut1_65)}",
            "ut1_midpoint_65pct": seconds_to_pace(p_ut1_65),
            "indicative_30r20_725pct": seconds_to_pace(p_30r20_725),
            "at_80pct": seconds_to_pace(p_at_80),
        },
        "warmup_paces": warmup_paces,
        "l4_table": l4_table,
    }


def align_session_pace_to_l4(athlete_id: str, intensity_zone: str) -> Dict[str, Any]:
    """Aligns a training zone to its midpoint split and closest L4 stroke rate.
    
    Implements Sam Dutney's guideline: For UT2, UT1, or AT sessions, align the
    required split towards the middle of the zone and match to the closest L4 stroke
    rate with an explicit split reminder for easy ErgData programming.
    
    Args:
        athlete_id: Athlete key (e.g. 'alex_martin', 'david_montgamery').
        intensity_zone: Target intensity ('UT2', 'UT1', or 'AT').
        
    Returns:
        Dictionary with zone midpoint, closest L4 stroke rate, exact L4 split,
        and ErgData reminder string.
    """
    res = lookup_athlete(athlete_id)
    if res.get("status") != "success":
        return res
    
    athlete = res["athlete"]
    split_2k_sec = pace_to_seconds(athlete["benchmark_2k_split"])
    zone_upper = intensity_zone.strip().upper()

    if zone_upper == "UT2":
        # UT2 is 50-60% 2k Watts, midpoint 55%
        target_pct = 0.55
        zone_desc = "UT2 (50–60% 2kW)"
    elif zone_upper == "UT1":
        # UT1 is 60-70% 2k Watts, midpoint 65%
        target_pct = 0.65
        zone_desc = "UT1 (60–70% 2kW)"
    elif zone_upper == "AT":
        # AT is 78-82% 2k Watts, midpoint 80%
        target_pct = 0.80
        zone_desc = "AT (78–82% 2kW)"
    else:
        return {
            "status": "error",
            "message": f"Unsupported intensity zone '{intensity_zone}'. Please choose 'UT2', 'UT1', or 'AT'.",
        }

    midpoint_split_sec = calculate_percent_split(split_2k_sec, target_pct)
    best_rate, l4_split_sec = get_closest_l4_rate(split_2k_sec, midpoint_split_sec)

    l4_split_str = seconds_to_pace(l4_split_sec)
    midpoint_split_str = seconds_to_pace(midpoint_split_sec)

    reminder_fr = f"allure L4 @{best_rate} ({l4_split_str})"
    reminder_en = f"L4 @{best_rate} pace ({l4_split_str})"

    return {
        "status": "success",
        "athlete_id": athlete["id"],
        "name": athlete["name"],
        "zone": zone_desc,
        "target_percent_watts": f"{int(target_pct * 100)}%",
        "midpoint_split": midpoint_split_str,
        "aligned_stroke_rate": best_rate,
        "aligned_l4_split": l4_split_str,
        "delta_sec": round(l4_split_sec - midpoint_split_sec, 2),
        "prescription_reminder_fr": reminder_fr,
        "prescription_reminder_en": reminder_en,
    }



# ==============================================================================
# Macrocycle Curriculum & Season Progression Engine
# ==============================================================================

CURRICULUM_FILE = PLANS_DIR / "block_1_curriculum.json"
SEASON_FILE = PLANS_DIR / "season_curriculum.json"

WEEKS_SCHEDULE: Dict[int, Dict[str, Any]] = {
    1: {
        "start": "2026-09-07",
        "end": "2026-09-13",
        "dates_fr": "Du 7 au 13 Septembre 2026",
        "block": 1,
        "block_name": "Fondation & Calibrage Drag Factor",
        "deload": False,
    },
    2: {
        "start": "2026-09-14",
        "end": "2026-09-20",
        "dates_fr": "Du 14 au 20 Septembre 2026",
        "block": 1,
        "block_name": "Batterie de Tests Sam Dutney (30r20, 60s, 7 coups)",
        "deload": False,
    },
    3: {
        "start": "2026-09-21",
        "end": "2026-09-27",
        "dates_fr": "Du 21 au 27 Septembre 2026",
        "block": 1,
        "block_name": "Pic de Volume Bloc 1 (Vitesse & Puissance)",
        "deload": False,
    },
    4: {
        "start": "2026-09-28",
        "end": "2026-10-04",
        "dates_fr": "Du 28 Septembre au 4 Octobre 2026",
        "block": 1,
        "block_name": "Décharge d'Assimilation Bloc 1 (-30% Volume)",
        "deload": True,
    },
    5: {
        "start": "2026-10-05",
        "end": "2026-10-11",
        "dates_fr": "Du 5 au 11 Octobre 2026",
        "block": 2,
        "block_name": "Seuil Anaérobie Bloc 2",
        "deload": False,
    },
    6: {
        "start": "2026-10-12",
        "end": "2026-10-18",
        "dates_fr": "Du 12 au 18 Octobre 2026",
        "block": 2,
        "block_name": "Dutney Full AT (4x8') & Wolverine L2 (3x2000m)",
        "deload": False,
    },
    7: {
        "start": "2026-10-19",
        "end": "2026-10-25",
        "dates_fr": "Du 19 au 25 Octobre 2026",
        "block": 2,
        "block_name": "Pic de Volume Bloc 2 & Surcharge Capacité Anaérobie (6x45s)",
        "deload": False,
    },
    8: {
        "start": "2026-10-26",
        "end": "2026-11-01",
        "dates_fr": "Du 26 Octobre au 1 Novembre 2026",
        "block": 2,
        "block_name": "Décharge d'Assimilation 2 & Benchmark Officiel 2000m Chrono",
        "deload": True,
    },
    9: {
        "start": "2026-11-02",
        "end": "2026-11-08",
        "dates_fr": "Du 2 au 8 Novembre 2026",
        "block": 3,
        "block_name": "Endurance de Vitesse Supramaximale L1 (8x500m)",
        "deload": False,
    },
    10: {
        "start": "2026-11-09",
        "end": "2026-11-15",
        "dates_fr": "Du 9 au 15 Novembre 2026",
        "block": 3,
        "block_name": "Rythme de Course L1 (4x1000m) & Capacité Anaérobie (5x1')",
        "deload": False,
    },
    11: {
        "start": "2026-11-16",
        "end": "2026-11-22",
        "dates_fr": "Du 16 au 22 Novembre 2026",
        "block": 3,
        "block_name": "Puissance Aérobie Critique (Protocole Dutney / Tabata 8x20/10)",
        "deload": False,
    },
    12: {
        "start": "2026-11-23",
        "end": "2026-11-29",
        "dates_fr": "Du 23 au 29 Novembre 2026",
        "block": 3,
        "block_name": "Mécanique des Départs 5 Coups & Précision d'Allure (2x1250m)",
        "deload": False,
    },
    13: {
        "start": "2026-11-30",
        "end": "2026-12-06",
        "dates_fr": "Du 30 Novembre au 6 Décembre 2026",
        "block": 3,
        "block_name": "Semaine de Compétition (Décharge 3 & Répétition Générale Manosque 6 Décembre)",
        "deload": True,
    },
}

COMPETITIONS_DATA: Dict[str, Dict[str, Any]] = {
    "manosque": {
        "name": "Championnat Régional d'Aviron Indoor (Manosque)",
        "date": "2026-12-06 (Fin de Semaine 13)",
        "tier": "Tier 2 (Priority B)",
        "role": "Secondary Objective / Operational Dress Rehearsal for Paris",
        "distance": "2,000m Concept2 Static Ergometer",
        "taper_protocol": "48-72h active micro-taper (train-through, volume maintenu pour préserver la base aérobie hivernale, PAS de grand affûtage de 2 semaines)",
    },
    "paris": {
        "name": "Championnat de France d'Aviron Indoor (Paris)",
        "date": "Late January 2027 (~Semaine 20)",
        "tier": "Tier 1 (Priority A)",
        "role": "Primary Objective / Winter Championship Peak Target",
        "distance": "2,000m Concept2 Static Ergometer",
        "taper_protocol": "10-14 day exponential taper (-40% à -50% de volume, conservation de l'intensité et haute fréquence motrice)",
    },
}


def _resolve_week_number(week_num: Optional[int] = None, date_str: Optional[str] = None) -> int:
    """Helper to resolve week number from explicit int or date string."""
    if week_num is not None:
        try:
            w = int(week_num)
            if 1 <= w <= 13:
                return w
        except (ValueError, TypeError):
            pass

    if date_str:
        import re
        q = str(date_str).lower().strip()
        iso_match = re.search(r"2026-(\d{2})-(\d{2})", q)
        if iso_match:
            iso_val = f"2026-{iso_match.group(1)}-{iso_match.group(2)}"
            for w, info in WEEKS_SCHEDULE.items():
                if info["start"] <= iso_val <= info["end"]:
                    return w
        if "5" in q and ("11" in q or "oct" in q):
            return 5
        if "28" in q and ("oct" in q or "sep" in q):
            return 4
        if "21" in q and ("27" in q or "sep" in q):
            return 3
        if "12" in q and ("18" in q or "oct" in q):
            return 6
        if "19" in q and ("25" in q or "oct" in q):
            return 7
        if "26" in q and ("nov" in q or "oct" in q):
            return 8
        if "2" in q and ("8" in q or "nov" in q):
            return 9
        if "9" in q and ("15" in q or "nov" in q):
            return 10
        if "16" in q and ("22" in q or "nov" in q):
            return 11
        if "23" in q and ("29" in q or "nov" in q):
            return 12
        if "30" in q or ("6" in q and ("dec" in q or "déc" in q or "manosque" in q)):
            return 13

    # Default to current date context (October 3, 2026 -> Week 4)
    return 4


def get_season_schedule(
    week_number: Optional[int] = None,
    target_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieves the authoritative 13-week Phase 1 training plan and season progression.
    
    Provides the exact block, focus, deload status, session priorities (P1-P4), 
    and competition targets for any week or date.
    
    Args:
        week_number: Optional week number (1 to 13). If omitted and target_date is omitted, 
                     returns the current week based on today's date (Oct 3, 2026 -> Week 4; upcoming week -> Week 5).
        target_date: Optional ISO date string or natural language date (e.g. '2026-10-06' or 'Du 5 au 11 Octobre 2026').
        
    Returns:
        Dictionary containing current progression, week metadata, deload status, 
        detailed session priorities, and competition hierarchy (Manosque = Tier 2 Rehearsal, Paris = Tier 1 Peak).
    """
    resolved_w = _resolve_week_number(week_num=week_number, date_str=target_date)
    week_info = WEEKS_SCHEDULE.get(resolved_w, WEEKS_SCHEDULE[5])

    # Load detailed session curriculum
    sessions = []
    week_focus = ""
    target_volume = 4.0
    if CURRICULUM_FILE.exists():
        try:
            with open(CURRICULUM_FILE, "r", encoding="utf-8") as f:
                curriculum_data = json.load(f)
            w_data = curriculum_data.get("weeks", {}).get(str(resolved_w), {})
            week_focus = w_data.get("focus", "")
            target_volume = w_data.get("target_volume_hours", 4.0)
            sessions = w_data.get("sessions", [])
        except Exception:
            pass

    # Built-in fallbacks if sessions empty
    if not sessions:
        if resolved_w == 5:
            week_focus = "Sam Dutney Anaerobic Threshold (AT) Introduction & Wolverine L4 Overload"
            sessions = [
                {
                    "priority": 1,
                    "title": "Dutney Anaerobic Threshold (AT) Intro",
                    "warmup": "Protocole C (4000m, sprints à 90% 2kW)",
                    "main_workout": "3 x 7 min @ AT (Rest: 1:00 active paddle) à 24-26 spm",
                    "target_watts_pct": "78% to 82% Watts 2k",
                    "target_spm": [24, 26],
                    "cooldown": "10 min récupération légère @ 14-16 SPM",
                },
                {
                    "priority": 2,
                    "title": "Wolverine L4 Progressive Overload",
                    "warmup": "Protocole A (2000m)",
                    "main_workout": "5 x 10 min continu (Séquences 180 / 184 / 180 / 184 / 186 = 50 min)",
                    "target_watts_pct": "58% to 76% Watts 2k (Tables L4)",
                    "target_spm": [16, 18, 20, 22, 24],
                    "cooldown": "10 min Ergometer Flush @ 14-16 SPM",
                },
                {
                    "priority": 3,
                    "title": "Circuit Renforcement Postural & Fonctionnel",
                    "warmup": "Routine de Mobilité au Sol (5 min)",
                    "main_workout": "4 tours complets de Circuit Renforcement (45s effort / 15s transition)",
                    "target_watts_pct": "N/A",
                    "target_spm": [],
                    "cooldown": "Décompression articulaire et étirements",
                },
                {
                    "priority": 4,
                    "title": "Base Aérobie Continue (UT2)",
                    "warmup": "Protocole A (2000m)",
                    "main_workout": "55 min continu à rythme régulier (FC < 75% FCmax)",
                    "target_watts_pct": "52% to 58% Watts 2k",
                    "target_spm": [18, 22],
                    "cooldown": "Étirements passifs et hydratation",
                },
            ]

    return {
        "status": "success",
        "current_date": "2026-10-03",
        "current_progression": {
            "current_week": 4,
            "current_week_name": "Semaine 4 - Décharge d'Assimilation Bloc 1 (-30% Volume)",
            "current_week_dates": "Du 28 Septembre au 4 Octobre 2026",
            "upcoming_week": 5,
            "upcoming_week_name": "Semaine 5 - Seuil Anaérobie Bloc 2",
            "upcoming_week_dates": "Du 5 au 11 Octobre 2026",
            "upcoming_week_is_deload": False,
            "upcoming_week_note": "Week 5 starts Block 2 (Anaerobic Threshold Intro 3x7' + Wolverine L4 Sequence 186). IT IS NOT A DELOAD WEEK.",
        },
        "requested_week": resolved_w,
        "week_metadata": {
            "week_number": resolved_w,
            "block_number": week_info["block"],
            "block_name": week_info["block_name"],
            "dates_fr": week_info["dates_fr"],
            "start_date": week_info["start"],
            "end_date": week_info["end"],
            "is_deload_week": week_info["deload"],
            "focus": week_focus,
            "target_volume_hours": target_volume,
        },
        "sessions": sessions,
        "competitions": COMPETITIONS_DATA,
        "curriculum_rules": {
            "phase_1_duration": "13 semaines (du 7 Septembre au 6 Décembre 2026)",
            "deload_weeks": [4, 8, 13],
            "manosque_status": "Tier 2 (Priority B) Rehearsal - Secondary squad objective. Dec 6, 2026 (End of Week 13). 48-72h active micro-taper, train-through.",
            "paris_status": "Tier 1 (Priority A) National Peak - Primary squad objective. Late January 2027 (~Week 20). 10-14 day exponential taper.",
        },
    }


# ==============================================================================
# Canonical Gospel Resources (Weeks 2–4 Master Protocols)
# ==============================================================================

GOSPEL_MOBILITY_ROUTINE = """### **1. Routine de Mobilité au Sol (5 Minutes avant de ramer ou le circuit de Renforcement)** 

* **Dorsiflexion cheville :** 8-10 reps sans décoller le talon. [Voir le tutoriel](https://youtu.be/ldqCAkdsGnU?si=toftVT96HcIkzjgO)  
* **Hip Hinge (Basculement bassin) :** 15-20 reps debout. [Voir le tutoriel](https://www.youtube.com/watch?v=4EpehztVrxo&t=322s)  
* **Élévations Y-W-T au sol (à plat ventre ou début) :** 8-10 reps. [Voir le tutoriel](https://www.youtube.com/watch?v=4EpehztVrxo&t=372s)  
* **Ponts fessiers (Glute Bridges) :** 12-15 reps.  [Voir la vidéo](https://www.youtube.com/watch?v=43qFZebHGj8)  
* **Fente Spider-Man :** 5 reps par côté. [Voir le tutoriel](https://www.youtube.com/watch?v=il1AhduOqfA)  
* **Pompes contrôlées :** 8-10 reps.  [Voir le tutoriel](https://youtu.be/ryncZFQCB8I), les progressions proposés devraient rendre les pompes possible pour presque tout le monde  
* **Progression de Squats profonds :** 8-10 reps. [Voir le tutoriel](https://www.youtube.com/watch?v=4EpehztVrxo&t=460s)"""

GOSPEL_WARMUP_PROTOCOLS = """### **2. Protocoles d'Échauffement sur Ergomètre**

[Vidéo Référence : Reverse Pick Drill / Échauffement Ergo](https://www.youtube.com/watch?v=ssPQK-Qh57U) (pour jambes seuls et jambes plus dos)  

**Protocole A : Pour le Test 30' R20 et les séances d'endurance (2000m)**  
[**https://workout.ergdata.com/shared/b9xdw96z**](https://workout.ergdata.com/shared/b9xdw96z) 

* **0-300m (Reverse Pick Drill) :** 30s jambes seuls / 30s jambes + dos / 30s bras-corps / 30s demi-coulisse.  
* **300-500m :** @26-27 @ 70% Watts 2k (Allure UT1 soutenue).  
* **500-1150m :** @14-16 (Allure de récupération 14-16).  
* **1150-1300m :** @28-30 @ 90% Watts 2k (12-15 coups forts).  
* **1300-1750m :** @14-16 (Récupération 14-16).  
* **1750-1900m :** @26-27 @ 80% Watts 2k (12-15 coups forts).  
* **1900-2300m :** @14-16 (Récupération 14-16).  
* *Prendre 2 minutes de repos pour boire et régler le moniteur.*

**Protocole B : Pour les Tests de Puissance et Sprints (4000m)**  
[**https://workout.ergdata.com/shared/dbdb3xzv**](https://workout.ergdata.com/shared/dbdb3xzv) 

* **0-300m (Reverse Pick Drill) :** 30s jambes seuls / 30s jambes + dos / 30s bras-corps / 30s demi-coulisse.  
* **300-400m :** @26-27 @ 70% Watts 2k.  
* **400-1300m :** @14-16 SM (Récupération 14-16).  
* **1300-2300m :** @26-27 @ >80% Watts 2k (Effort continu, pas de petits sprints).  
* **2300-2800m :** @18 (Cible : Allure de cadence 16 L4).  
* **2800-3100m :** @14-16 (Récupération 14-16).  
* **3100-3300m :** @32-33 @ 105% Watts 2k.  
* **3300-3750m :** @14-16 (Récupération 14-16).  
* **3750-3900m :** @32-33 @ 105% Watts 2k.  
* **3900-4300m :** @14-16 (Récupération 14-16).

**Protocole C : Pour le Seuil Anaérobie (AT) (4000m)**  
*(Structure identique au Protocole B, avec les 2 accélérations de 200m et 150m à 90% des Watts 2k au lieu de 105% pour une activation aérobie optimale sans acidose prématurée).*

* **0-300m (Reverse Pick Drill) :** 30s jambes seuls / 30s jambes + dos / 30s bras-corps / 30s demi-coulisse.  
* **300-400m :** @26-27 @ 70% Watts 2k.  
* **400-1300m :** @14-16 SM (Récupération 14-16).  
* **1300-2300m :** @26-27 @ >80% Watts 2k (Effort continu, pas de petits sprints).  
* **2300-2800m :** @18 (Cible : Allure de cadence 16 L4).  
* **2800-3100m :** @14-16 (Récupération 14-16).  
* **3100-3300m :** @28-30 @ 90% Watts 2k.  
* **3300-3750m :** @14-16 (Récupération 14-16).  
* **3750-3900m :** @28-30 @ 90% Watts 2k.  
* **3900-4300m :** @14-16 (Récupération 14-16)."""

GOSPEL_STRENGTH_CIRCUIT = """#### **Priorité 3 (Circuit Renforcement) :**

*Format : 45s effort strict / 15s transition. Repos 90s entre chaque tour. 4 tours complets. La Routine de Mobilité au Sol est à faire avant comme échauffement !*

* **Squats poids du corps descente lente :** Descente lente, dos neutre. [Voir le tutoriel](https://www.youtube.com/watch?v=mGvzVjuY8SY)  
* **Pompes Contrôlées au sol :** Corps gainé. [Voir le tutoriel](https://youtu.be/ryncZFQCB8I)  
* **Seated Rockback :** Gainage excentrique spécifique aviron (Assis, dos droit, basculer vers l'arrière en retenant avec les abdos). [Voir l'exercice d'activation tronc/bassin](https://youtu.be/qgPflK29jb0)  
* **Glute Bridges (Pont fessier) :** Pousser sur les talons. [Voir le tutoriel](https://www.youtube.com/watch?v=43qFZebHGj8)  
* **Bird-Dog (Gainage) :** Pas de rotation du bassin. [Voir le tutoriel](https://youtu.be/BP-dCHOqRVY?si=9ImhLwv8ZMHgI-3Y)"""


# ==============================================================================
# Weekly Sheet & Coaching Notes Generators
# ==============================================================================

def generate_weekly_sheet(
    week_number: int = 5,
    block_name: Optional[str] = None,
    dates_str: Optional[str] = None,
    priority_1_desc: Optional[str] = None,
    priority_2_desc: Optional[str] = None,
    priority_3_desc: Optional[str] = None,
    priority_4_desc: Optional[str] = None,
) -> str:
    """Generates the full French squad markdown file strictly matching Week 3 & 4 templates.
    
    Embeds the canonical Ground Mobility Routine, Warmup Protocols (A, B, and C with 90% 2kW AT priming),
    the complete Bodyweight Functional Circuit (with tutorial links), and personalized athlete cards.
    
    Args:
        week_number: The week number (1 to 13, default 5).
        block_name: Optional block title in French. If omitted, fetched from official curriculum.
        dates_str: Optional calendar dates string. If omitted, fetched from official curriculum.
        priority_1_desc: Optional high-level summary of Priority 1 session.
        priority_2_desc: Optional high-level summary of Priority 2 session.
        priority_3_desc: Optional high-level summary of Priority 3 session.
        priority_4_desc: Optional high-level summary of Priority 4 session.
        
    Returns:
        Absolute file path where the weekly sheet was saved.
    """
    sched = get_season_schedule(week_number=week_number)
    meta = sched["week_metadata"]
    sess_list = sched["sessions"]

    b_name = block_name or meta.get("block_name", f"Bloc {meta.get('block_number', 2)}")
    d_str = dates_str or meta.get("dates_fr", "Du 5 au 11 Octobre 2026")

    # Priority descriptions fallback
    p1 = priority_1_desc
    p2 = priority_2_desc
    p3 = priority_3_desc
    p4 = priority_4_desc

    if not p1 and len(sess_list) >= 1:
        s = sess_list[0]
        p1 = f"{s.get('title')} : {s.get('main_workout')} ({s.get('warmup')})"
    if not p2 and len(sess_list) >= 2:
        s = sess_list[1]
        p2 = f"{s.get('title')} : {s.get('main_workout')}"
    if not p3 and len(sess_list) >= 3:
        s = sess_list[2]
        p3 = f"{s.get('title')} : {s.get('main_workout')}"
    if not p4 and len(sess_list) >= 4:
        s = sess_list[3]
        p4 = f"{s.get('title')} : {s.get('main_workout')}"

    # Default fallbacks if still empty
    p1 = p1 or "Échauffement Protocole C. 3 x 7 min @ AT (Rest: 1:00 active paddle) à 24-26 spm. Récupération 10 min 14-16."
    p2 = p2 or "Échauffement Protocole A. 5 x 10 min continu (Séquences 180 / 184 / 180 / 184 / 186 = 50 min unbroken)."
    p3 = p3 or "Circuit Renforcement Postural & Fonctionnel (4 tours complets, 45s effort / 15s transition)."
    p4 = p4 or "Échauffement Protocole A. 55 min continu aérobie UT2 régulier (FC < 75% FCmax, cadence 18-22 spm)."

    athletes = _load_athletes()

    filename = f"Semaine {week_number} - {b_name} ({d_str}).md"
    file_path = PLANS_DIR / filename

    content_lines = []
    content_lines.append("# **CASC - Programme Master : Fiches Individuelles**")
    content_lines.append("")
    content_lines.append(f"**Semaine {week_number} - {b_name} ({d_str})**")
    content_lines.append("")
    content_lines.append("*Note à tous : Les cibles de puissance (Watts) et les allures pour cette semaine ont étés calculées en fonction de vos tests récents et de votre référence 2k. Maintenez scrupuleusement vos réglages de Drag Factor sur l'écran PM5.*")
    content_lines.append("")
    content_lines.append("## **📚 Ressources et Protocoles de la Semaine**")
    content_lines.append("")
    content_lines.append(GOSPEL_MOBILITY_ROUTINE)
    content_lines.append("")
    content_lines.append(GOSPEL_WARMUP_PROTOCOLS)
    content_lines.append("")
    content_lines.append("### **3. Séances**")
    content_lines.append("")
    content_lines.append(f"#### **3.1 Priorité 1 :**\n\n* {p1}")
    content_lines.append("")
    content_lines.append(f"#### **3.2 Priorité 2 :**\n\n* {p2}")
    content_lines.append("")
    content_lines.append(GOSPEL_STRENGTH_CIRCUIT)
    content_lines.append("")
    content_lines.append(f"#### **3.4 Priorité 4 :**\n\n* {p4}")
    content_lines.append("")
    content_lines.append("## **🚣 Fiches Individuelles & Allures Cibles**")
    content_lines.append("")

    for aid, a in athletes.items():
        pacing = calculate_athlete_pacing(aid)
        name = a["name"]
        fc_max = a.get("hr_max", "N/A")
        df = a.get("drag_factor", "120–123")
        cross_training = a.get("cross_training", "None")
        zone1_cap = a.get("hr_zone1_cap", "N/A")
        if zone1_cap != "N/A" and isinstance(zone1_cap, (int, float)):
            zone1_cap_str = f"{int(round(zone1_cap))} bpm"
        else:
            zone1_cap_str = str(zone1_cap)

        at_align = align_session_pace_to_l4(aid, "AT")
        ut2_align = align_session_pace_to_l4(aid, "UT2")

        content_lines.append(f"### **{name}**")
        content_lines.append("")
        content_lines.append(f"* **Fréquence Cardiaque max : {fc_max}**  ")
        content_lines.append(f"* **Drag Factor :** {df}  ")
        if cross_training and cross_training != "None":
            content_lines.append(f"* **Ajustement Cross-Training :** Activité déclarée : {cross_training}. La Priorité 3 (PPG) peut être adaptée ou remplacée par ta séance Avi-fit au club selon ton planning.")
        content_lines.append("")

        # Zone Table matching Weeks 3 & 4
        content_lines.append("| Zone | Intensité (% W2k) | Cadence | Allure /500m |")
        content_lines.append("| :---- | :---: | :---: | :---: |")
        content_lines.append(f"| **Nouveau 2k Réf.** | 100% | Course | {pacing['benchmark_2k_split']}  |")
        content_lines.append(f"| **Seuil AT (P1)** | 78-82% | 24-26 | {pacing['zones']['at_80pct']}  |")
        content_lines.append(f"| **Récup 14-16** | ~36% | 14-16 | {pacing['zones']['recovery_14_16']}  |")
        content_lines.append(f"| **UT3 (HR < 65% HRmax)** | 40-50% | 16-18 | {pacing['zones']['ut3_range']}  |")
        content_lines.append(f"| **UT2 (HR < 75% HRmax)** | 50-60% | 18-22 | {pacing['zones']['ut2_range']}  |")
        content_lines.append(f"| **70% 2kW (échauff A, B, C)** | 70% | 26-27 | {pacing['warmup_paces']['70%']}  |")
        content_lines.append(f"| **80% 2kW (échauff A, B, C)** | 80% | 26-27 | {pacing['warmup_paces']['80%']}  |")
        content_lines.append(f"| **90% 2kW (échauff A et C)** | 90% | 28-30 | {pacing['warmup_paces']['90%']}  |")
        content_lines.append(f"| **105% 2kW (échauff B)** | 105% | 32-33 | {pacing['warmup_paces']['105%']}  |")
        content_lines.append("")

        # Wolverine L4 Table with all cadences
        content_lines.append("**Table d'allures L4 (Wolverine Plan) :**")
        content_lines.append("")
        content_lines.append("| Cadence L4 | @16 | @17 | @18 | @19 | @20 | @21 | @22 | @24 | @26 | @28 |")
        content_lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
        l4_vals = [
            pacing["l4_table"].get("@16", ""),
            pacing["l4_table"].get("@17", ""),
            pacing["l4_table"].get("@18", ""),
            pacing["l4_table"].get("@19", ""),
            pacing["l4_table"].get("@20", ""),
            pacing["l4_table"].get("@21", ""),
            pacing["l4_table"].get("@22", ""),
            pacing["l4_table"].get("@24", ""),
            pacing["l4_table"].get("@26", ""),
            pacing["l4_table"].get("@28", ""),
        ]
        content_lines.append(f"| Allure /500m | {' | '.join(l4_vals)} |")
        content_lines.append("")

        # Personalized program for this athlete
        p1_reminder = at_align.get("prescription_reminder_fr", f"allure {pacing['zones']['at_80pct']}")
        p4_reminder = ut2_align.get("prescription_reminder_fr", f"allure {pacing['zones']['ut2_range']}")

        p3_text = "Avi-Fit ou 4 tours de circuit PPG complet." if "avifit" in cross_training.lower() else "4 tours de circuit PPG complet."

        content_lines.append("* **Programme :**  ")
        content_lines.append(f"  * **Priorité 1 :** Échauffement Protocole C. **3 x 7 min @ AT (Rest: 1:00 active paddle)**. Cadence 24-26. Cible : {p1_reminder} ou 80% 2kW ({pacing['zones']['at_80pct']}). Récupération 10 min à votre allure de récup 14-16 ({pacing['zones']['recovery_14_16']}).  ")
        content_lines.append(f"  * **Priorité 2 :** Échauffement Protocole A. **5 x 10 min continu (Séquences 180/184/180/184/186).** Respecter scrupuleusement les cadences et allures L4 du tableau ci-dessus. Récupération 10 min à votre allure de récup 14-16 ({pacing['zones']['recovery_14_16']}).  ")
        content_lines.append(f"  * **Priorité 3 :** {p3_text}  ")
        content_lines.append(f"  * **Priorité 4 :** Échauffement Protocole A. **55 min continu.** Cadence 18-22. Cible : {p4_reminder} ou 50-60% W2k. FC < {zone1_cap_str} ! Récupération 10 min à votre allure de récup 14-16 ({pacing['zones']['recovery_14_16']}).")
        content_lines.append("")

    content = "\n".join(content_lines)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    return str(file_path)


def generate_coach_notes(
    week_number: int = 5,
    block_name: Optional[str] = None,
    methodology_focus: Optional[str] = None,
    athlete_checkpoints: Optional[Dict[str, str]] = None,
) -> str:
    """Generates the high-level English coaching analysis and methodology notes.
    
    Args:
        week_number: The week number (1 to 13, default 5).
        block_name: Optional block title in English (default 'Anaerobic Threshold Expansion').
        methodology_focus: Optional detailed explanation of Sam Dutney physiological targets.
        athlete_checkpoints: Optional mapping of athlete_id -> specific checkpoint or limiter.
        
    Returns:
        Absolute file path where the coach notes were saved.
    """
    b_name = block_name or "Anaerobic Threshold Expansion"
    focus_text = methodology_focus or (
        "Initiating Sam Dutney's Anaerobic Threshold (AT) protocol with 3 x 7 min intervals on short 1:00 active "
        "recovery paddles. Short recovery forces lactate shuttling via MCT1 slow-twitch transporters under continuous load, "
        "shifting the lactate turning curve to the right. Concurrently, Wolverine L4 introduces Sequence 186 in a full "
        "50-minute continuous row to develop stroke-by-stroke power durability."
    )
    checkpoints = athlete_checkpoints or {
        "alex_martin": "Watch for cadence drift on rep 3 of AT. Ensure L4 @21 split (2:27.1) remains steady.",
        "sophie_dubois": "Balance Avi-fit cross-training with L4 sequences. Protect lumbar alignment on @16 and @18.",
        "david_montgamery": "Lock AT 3x7' firmly at 78-82% 2k Watts (~2:07-2:09/500m). Guard against early sprint overshoot.",
        "florence_moreau": "Coordinate Avi-fit with P1 AT pieces. Maintain 103-106 drag factor compliance.",
        "lucas_bernard": "Enforce strict layback control on L4 low rates (@16 and @18). Avoid rushing the slide.",
    }

    filename = f"Week {week_number} - Coach Analysis & Methodology Notes.md"
    file_path = PLANS_DIR / filename

    lines = []
    lines.append(f"# Coach Technical Analysis & Methodology Notes - Week {week_number}")
    lines.append(f"### {b_name}")
    lines.append("")
    lines.append("## 1. Physiological Objectives & Energy Systems")
    lines.append(focus_text)
    lines.append("")
    lines.append("## 2. Sam Dutney Rule Enforcement & Methodology")
    lines.append("- **Protected High-Intensity**: Priority 1 represents the singular protected threshold stimulus of the week.")
    lines.append("- **Recovery Buffer**: Aim for a 48-hour recovery buffer between high-intensity sessions; ask for 72 hours if possible (lighter sessions such as UT2/UT3 or mobility can be scheduled in between) to ensure adequate parasympathetic tone and CNS readiness.")
    lines.append("- **UT2 Shielding**: All supplementary base volume is capped strictly below the first lactate threshold (<2.0 mmol/L, Borg RPE 4/10).")
    lines.append("- **Triangulation Triad**: Rowers must cross-check Split, Heart Rate, and RPE. If 2 out of 3 match, the zone is confirmed.")
    lines.append("- **Virtual Lactate Ear-Prick**: Rowers must not chase HR stroke-by-stroke. Lock split across pieces, check HR during pauses.")
    lines.append("")
    lines.append("## 3. Competition Horizon & Strategic Calendar")
    lines.append("- **Secondary Objective (Tier 2 / Priority B)**: **Championnat Régional d'Aviron Indoor in Manosque on December 6, 2026** (end of Week 13). Rehearsal / benchmark under race conditions with a 48–72h active micro-taper (train-through approach preserving chronic winter aerobic base).")
    lines.append("- **Primary Objective (Tier 1 / Priority A)**: **Championnat de France d'Aviron Indoor in Paris in late January 2027** (~Week 20). Full 10–14 day exponential taper (40–50% volume drop) for maximal supercompensation.")
    lines.append("")
    lines.append("## 4. Athlete-Specific Checkpoints & Limiter Tracking")
    lines.append("")
    for aid, checkpoint in checkpoints.items():
        res = lookup_athlete(aid)
        name = res["athlete"]["name"] if res.get("status") == "success" else aid
        lines.append(f"- **{name}**: {checkpoint}")
    lines.append("")
    lines.append("---")
    lines.append("*Generated by ADK Rowing Coach Master Agent for Coach David Montgamery.*")

    content = "\n".join(lines)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    return str(file_path)

