"""Unit tests for rowing_coach tools and physics calculations."""

import os
import pathlib
import pytest
from rowing_coach.tools import (
    pace_to_seconds,
    seconds_to_pace,
    split_to_watts,
    watts_to_split,
    calculate_percent_split,
    calculate_recovery_split,
    calculate_l4_split,
    get_closest_l4_rate,
    lookup_athlete,
    calculate_athlete_pacing,
    align_session_pace_to_l4,
    get_season_schedule,
    generate_weekly_sheet,
    generate_coach_notes,
)


def test_pace_conversions():
    assert pace_to_seconds("2:10.0") == 130.0
    assert pace_to_seconds("1:54.9") == 114.9
    assert seconds_to_pace(130.0) == "2:10.0"
    assert seconds_to_pace(130.0, include_tenths=False) == "2:10"


def test_concept2_physics():
    # 2:00.0 (120s) is known standard: 2.80 / (120/500)^3 = 202.5 Watts
    w = split_to_watts(120.0)
    assert round(w, 1) == 202.5
    s = watts_to_split(w)
    assert round(s, 1) == 120.0


def test_recovery_split_formula():
    # Example rower: 2k = 130.0s (2:10.0) -> recovery = 130.0 * 1.41 = 183.3s -> 3:03.3 (~3:03)
    recup_sec = calculate_recovery_split(130.0)
    assert round(recup_sec, 1) == 183.3
    assert seconds_to_pace(recup_sec, include_tenths=False) == "3:03"


def test_l4_stroke_rate_formula_alain():
    # Example 2k = 130.0s
    # R=16: 130.0 * 120/96 = 162.5s = 2:42.5
    # R=17: 130.0 * 118/96 = 159.79s = 2:39.8
    # R=18: 130.0 * 116/96 = 157.08s = 2:37.1
    # R=20: 130.0 * 112/96 = 151.67s = 2:31.7
    # R=22: 130.0 * 108/96 = 146.25s = 2:26.2 (or 2:26.3)
    # R=28: 130.0 * 96/96  = 130.0s  = 2:10.0
    s16 = calculate_l4_split(130.0, 16)
    s17 = calculate_l4_split(130.0, 17)
    s18 = calculate_l4_split(130.0, 18)
    s20 = calculate_l4_split(130.0, 20)
    s22 = calculate_l4_split(130.0, 22)
    s28 = calculate_l4_split(130.0, 28)

    assert seconds_to_pace(s16) == "2:42.5"
    assert seconds_to_pace(s17) == "2:39.8"
    assert seconds_to_pace(s18) == "2:37.1"
    assert seconds_to_pace(s20) == "2:31.7"
    assert seconds_to_pace(s22) == "2:26.2" or seconds_to_pace(s22) == "2:26.3"
    assert seconds_to_pace(s28) == "2:10.0"


def test_lookup_athlete():
    res = lookup_athlete("Alex")
    assert res["status"] == "success"
    assert "Martin" in res["athlete"]["name"]
    assert res["athlete"]["benchmark_2k_split"] == "2:08.4"

    res_david = lookup_athlete("david")
    assert res_david["status"] == "success"
    assert "David" in res_david["athlete"]["name"]

    res_celine = lookup_athlete("celine")
    assert res_celine["status"] == "success"
    assert "Céline" in res_celine["athlete"]["name"] or "Celine" in res_celine["athlete"]["name"]


def test_align_session_pace_to_l4():
    # Alex Martin UT1 (65% 2kW) -> midpoint split ~2:28.2 -> closest L4 is @21 (2:27.1) or @20 (2:29.8)
    align_ut1 = align_session_pace_to_l4("alex_martin", "UT1")
    assert align_ut1["status"] == "success"
    assert align_ut1["aligned_stroke_rate"] in (20, 21)
    assert "allure L4" in align_ut1["prescription_reminder_fr"]

    # AT (80% 2kW) -> 2:18.3 -> closest L4 is @24 or @25
    align_at = align_session_pace_to_l4("alex_martin", "AT")
    assert align_at["status"] == "success"
    assert align_at["aligned_stroke_rate"] in (24, 25)


def test_get_season_schedule_current_and_upcoming():
    res = get_season_schedule()
    assert res["status"] == "success"
    assert res["current_progression"]["current_week"] == 4
    assert res["current_progression"]["upcoming_week"] == 5
    assert res["current_progression"]["upcoming_week_is_deload"] is False

    # Week 5 explicit
    res_w5 = get_season_schedule(week_number=5)
    assert res_w5["week_metadata"]["week_number"] == 5
    assert res_w5["week_metadata"]["is_deload_week"] is False
    assert "Seuil Anaérobie" in res_w5["week_metadata"]["block_name"]
    assert len(res_w5["sessions"]) == 4

    # Check P1 workout in week 5
    p1 = res_w5["sessions"][0]
    assert "3 x 7 min" in p1["main_workout"]

    # Deload week 4
    res_w4 = get_season_schedule(week_number=4)
    assert res_w4["week_metadata"]["is_deload_week"] is True


def test_get_season_schedule_by_date():
    res_date = get_season_schedule(target_date="Du 5 au 11 Octobre 2026")
    assert res_date["requested_week"] == 5
    assert res_date["week_metadata"]["is_deload_week"] is False

    res_iso = get_season_schedule(target_date="2026-10-08")
    assert res_iso["requested_week"] == 5


def test_competition_hierarchy():
    res = get_season_schedule()
    comps = res["competitions"]
    assert "manosque" in comps
    assert "paris" in comps

    manosque = comps["manosque"]
    assert "Tier 2" in manosque["tier"] or "Priority B" in manosque["tier"]
    assert "micro-taper" in manosque["taper_protocol"]

    paris = comps["paris"]
    assert "Tier 1" in paris["tier"] or "Priority A" in paris["tier"]
    assert "10-14 day" in paris["taper_protocol"]


def test_generate_weekly_sheet_and_coach_notes(tmp_path, monkeypatch):
    monkeypatch.setattr("rowing_coach.tools.PLANS_DIR", tmp_path)
    sheet_path = generate_weekly_sheet(
        week_number=5,
        block_name="Seuil Anaérobie Bloc 2",
        dates_str="Du 5 au 11 Octobre 2026",
    )
    assert os.path.exists(sheet_path)
    with open(sheet_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Gospel mobility checks
    assert "Dorsiflexion cheville" in content
    assert "Hip Hinge" in content
    assert "Élévations Y-W-T" in content
    assert "Ponts fessiers" in content
    assert "Fente Spider-Man" in content
    assert "Pompes contrôlées" in content
    assert "Progression de Squats profonds" in content

    # Gospel warmups checks
    assert "workout.ergdata.com/shared/b9xdw96z" in content
    assert "workout.ergdata.com/shared/dbdb3xzv" in content
    assert "Protocole C : Pour le Seuil Anaérobie (AT)" in content
    assert "90% des Watts 2k" in content

    # Gospel strength circuit checks
    assert "Priorité 3 (Circuit Renforcement)" in content
    assert "Seated Rockback" in content
    assert "Bird-Dog" in content

    # Athletes and L4 table checks
    assert "Alex Martin" in content
    assert "David Montgamery" in content
    assert "Sophie Dubois" in content
    assert "Table d'allures L4 (Wolverine Plan)" in content
    assert "@16" in content and "@28" in content

    notes_path = generate_coach_notes(
        week_number=5,
        block_name="Anaerobic Threshold Expansion",
        methodology_focus="Introduction to evidence-based AT intervals.",
        athlete_checkpoints={"alex_martin": "Monitor threshold drift.", "david_montgamery": "Check 78-82% power lock."},
    )
    assert os.path.exists(notes_path)
    with open(notes_path, "r", encoding="utf-8") as f:
        notes_content = f.read()
    assert "Manosque" in notes_content
    assert "Paris" in notes_content
    assert "48-hour" in notes_content

