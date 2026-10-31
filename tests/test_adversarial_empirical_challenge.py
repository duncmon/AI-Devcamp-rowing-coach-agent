"""Adversarial stress-test harness for Tool Output Pseudonymization, Error Boundaries,
and UI Theme Classes under GDPR Article 9.

Executed by teamwork_challenger_m1_2.
"""

import json
import re
from pathlib import Path
from typing import Any, List, Set
import pytest

from rowing_coach.tools import (
    lookup_athlete,
    get_squad_roster,
    calculate_athlete_pacing,
    align_session_pace_to_l4,
    ATHLETES_FILE,
)
from local_proxy import HTML_PAGE


@pytest.fixture(scope="module")
def roster_ground_truth():
    """Load all raw IDs, cleartext names, and pseudonyms directly from athletes.json."""
    with open(ATHLETES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    athletes = data.get("athletes", [])
    raw_ids = [a["id"] for a in athletes]
    names = [a["name"] for a in athletes]
    pseudonyms = [a.get("pseudonym") for a in athletes if a.get("pseudonym")]
    tokens = [f"[ATHLETE_{p}]" for p in pseudonyms]
    return {
        "athletes": athletes,
        "raw_ids": raw_ids,
        "names": names,
        "pseudonyms": pseudonyms,
        "tokens": tokens,
    }


def find_pii_leaks(obj: Any, banned_strings: Set[str], path: str = "") -> List[str]:
    """Recursively search for any banned string (cleartext name or raw ID) in obj."""
    leaks = []
    if isinstance(obj, str):
        for banned in banned_strings:
            if not banned:
                continue
            # Case-insensitive check for cleartext names or raw database keys
            # To avoid false positive substring matches on short tokens, check exact word/substring
            if banned.lower() in obj.lower():
                leaks.append(f"LEAK at {path}: found '{banned}' in '{obj}'")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            for banned in banned_strings:
                if banned and banned.lower() in str(k).lower():
                    leaks.append(f"LEAK in key at {path}.{k}: found '{banned}'")
            leaks.extend(find_pii_leaks(v, banned_strings, f"{path}.{k}"))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            leaks.extend(find_pii_leaks(item, banned_strings, f"{path}[{idx}]"))
    return leaks


# ==============================================================================
# 1. lookup_athlete Stress Tests
# ==============================================================================

def test_lookup_athlete_valid_tokens(roster_ground_truth):
    """Test lookup_athlete with valid bracketed tokens: [ATHLETE_ath_5a77195e], [ATHLETE_ath_db95573a]."""
    banned = set(roster_ground_truth["raw_ids"] + roster_ground_truth["names"])

    for token in ["[ATHLETE_ath_5a77195e]", "[ATHLETE_ath_db95573a]"]:
        res = lookup_athlete(token)
        assert res.get("status") == "success", f"Failed for token {token}: {res}"
        athlete = res.get("athlete", {})
        assert athlete.get("id") == token
        assert athlete.get("name") == token
        assert athlete.get("pseudonym") in token

        leaks = find_pii_leaks(res, banned)
        assert not leaks, f"PII leaks detected in lookup_athlete('{token}'): {leaks}"


def test_lookup_athlete_bare_hashes(roster_ground_truth):
    """Test lookup_athlete with bare hashes: ath_5a77195e, ath_db95573a."""
    banned = set(roster_ground_truth["raw_ids"] + roster_ground_truth["names"])

    for bare in ["ath_5a77195e", "ath_db95573a"]:
        res = lookup_athlete(bare)
        assert res.get("status") == "success", f"Failed for bare hash {bare}: {res}"
        athlete = res.get("athlete", {})
        assert athlete.get("id") == f"[ATHLETE_{bare}]"
        assert athlete.get("name") == f"[ATHLETE_{bare}]"
        assert athlete.get("pseudonym") == bare

        leaks = find_pii_leaks(res, banned)
        assert not leaks, f"PII leaks detected in lookup_athlete('{bare}'): {leaks}"


def test_lookup_athlete_coach_aliases(roster_ground_truth):
    """Test lookup_athlete with coach aliases: Coach David, coach, head coach, etc."""
    banned = set(roster_ground_truth["raw_ids"] + roster_ground_truth["names"])

    aliases = ["Coach David", "coach", "head coach", "athlete_name_1"]
    for alias in aliases:
        res = lookup_athlete(alias)
        assert res.get("status") == "success", f"Failed for alias {alias}: {res}"
        athlete = res.get("athlete", {})
        assert athlete.get("id") == "[ATHLETE_ath_5a77195e]"
        assert athlete.get("name") == "[ATHLETE_ath_5a77195e]"
        assert athlete.get("pseudonym") == "ath_5a77195e"

        leaks = find_pii_leaks(res, banned)
        assert not leaks, f"PII leaks detected in lookup_athlete('{alias}'): {leaks}"


def test_lookup_athlete_nonexistent_and_adversarial_queries(roster_ground_truth):
    """Verify that random strings and adversarial inputs return not_found without dumping roster."""
    banned = set(roster_ground_truth["raw_ids"] + roster_ground_truth["names"])

    queries = [
        "nonexistent_athlete",
        "random_user_999",
        "'; DROP TABLE athletes;--",
        "<script>alert('xss')</script>",
        "[ATHLETE_ath_deadbeef]",
        "[ATHLETE_NAME_99]",
        "zebra_lion_tiger",
    ]
    for q in queries:
        res = lookup_athlete(q)
        assert res.get("status") == "not_found", f"Expected not_found for query '{q}', got: {res}"
        msg = res.get("message", "")
        # Verify message never dumps the 16-person roster or any athlete names
        assert "get_squad_roster()" in msg
        leaks = find_pii_leaks(res, banned)
        assert not leaks, f"PII leaks detected in lookup_athlete('{q}'): {leaks}"


def test_lookup_athlete_multiple_matches_no_pii_dump(roster_ground_truth):
    """Verify that multiple_matches returns pseudonym tokens only and no cleartext roster."""
    banned = set(roster_ground_truth["raw_ids"] + roster_ground_truth["names"])

    # Query matching multiple athletes
    res = lookup_athlete("ath")
    if res.get("status") == "multiple_matches":
        assert "matches" in res
        for m in res["matches"]:
            assert m["id"].startswith("[ATHLETE_")
            assert m["name"].startswith("[ATHLETE_")
        leaks = find_pii_leaks(res, banned)
        assert not leaks, f"PII leaks detected in multiple_matches: {leaks}"


def test_lookup_athlete_all_16_athletes_by_token_and_hash(roster_ground_truth):
    """Stress test: query all 16 athletes using both token and bare hash, asserting zero PII leaks."""
    banned = set(roster_ground_truth["raw_ids"] + roster_ground_truth["names"])

    for a in roster_ground_truth["athletes"]:
        pseudo = a["pseudonym"]
        token = f"[ATHLETE_{pseudo}]"

        # Query by token
        res_token = lookup_athlete(token)
        assert res_token.get("status") == "success"
        assert res_token["athlete"]["id"] == token
        assert res_token["athlete"]["name"] == token
        leaks = find_pii_leaks(res_token, banned)
        assert not leaks, f"Leaks for athlete {token}: {leaks}"

        # Query by bare hash
        res_bare = lookup_athlete(pseudo)
        assert res_bare.get("status") == "success"
        assert res_bare["athlete"]["id"] == token
        assert res_bare["athlete"]["name"] == token
        leaks = find_pii_leaks(res_bare, banned)
        assert not leaks, f"Leaks for athlete {pseudo}: {leaks}"


# ==============================================================================
# 2. get_squad_roster Stress Tests
# ==============================================================================

def test_get_squad_roster_all_16_cards(roster_ground_truth):
    """Test get_squad_roster: assert exactly 16 cards, 100% pseudonym tokens, 0 PII leaks."""
    banned = set(roster_ground_truth["raw_ids"] + roster_ground_truth["names"])

    res = get_squad_roster()
    assert res.get("status") == "success"
    assert res.get("total_athletes") == 16
    roster = res.get("squad_roster", [])
    assert len(roster) == 16

    for idx, card in enumerate(roster):
        token = card.get("id")
        assert str(token).startswith("[ATHLETE_ath_"), f"Card {idx} id not pseudonym token: {token}"
        assert card.get("name") == token, f"Card {idx} name not pseudonym token: {card.get('name')}"
        assert str(card.get("pseudonym")).startswith("ath_"), f"Card {idx} pseudonym invalid: {card.get('pseudonym')}"

    leaks = find_pii_leaks(res, banned)
    assert not leaks, f"PII leaks detected in get_squad_roster(): {leaks}"


# ==============================================================================
# 3. calculate_athlete_pacing Stress Tests
# ==============================================================================

def test_calculate_athlete_pacing_various_tokens(roster_ground_truth):
    """Test calculate_athlete_pacing with tokens, aliases, and bare hashes."""
    banned = set(roster_ground_truth["raw_ids"] + roster_ground_truth["names"])

    test_queries = [
        "[ATHLETE_ath_5a77195e]",
        "[ATHLETE_ath_db95573a]",
        "ath_5a77195e",
        "ath_db95573a",
        "Coach David",
    ]
    for q in test_queries:
        res = calculate_athlete_pacing(q)
        assert res.get("status") == "success", f"Failed pacing for {q}: {res}"
        assert str(res.get("athlete_id")).startswith("[ATHLETE_ath_")
        assert str(res.get("name")).startswith("[ATHLETE_ath_")
        assert "zones" in res
        assert "l4_table" in res

        leaks = find_pii_leaks(res, banned)
        assert not leaks, f"PII leaks detected in calculate_athlete_pacing('{q}'): {leaks}"


def test_calculate_athlete_pacing_all_16_athletes(roster_ground_truth):
    """Stress test: run pacing for all 16 squad members, asserting 100% token preservation."""
    banned = set(roster_ground_truth["raw_ids"] + roster_ground_truth["names"])

    for a in roster_ground_truth["athletes"]:
        token = f"[ATHLETE_{a['pseudonym']}]"
        res = calculate_athlete_pacing(token)
        assert res.get("status") == "success"
        assert res.get("athlete_id") == token
        assert res.get("name") == token

        leaks = find_pii_leaks(res, banned)
        assert not leaks, f"PII leaks detected in calculate_athlete_pacing('{token}'): {leaks}"


# ==============================================================================
# 4. align_session_pace_to_l4 Stress Tests
# ==============================================================================

def test_align_session_pace_to_l4_all_zones(roster_ground_truth):
    """Test align_session_pace_to_l4 across all supported intensity zones (UT2, UT1, AT)."""
    banned = set(roster_ground_truth["raw_ids"] + roster_ground_truth["names"])

    for zone in ["UT2", "UT1", "AT", "ut2", "ut1", "at"]:
        res = align_session_pace_to_l4("[ATHLETE_ath_5a77195e]", zone)
        assert res.get("status") == "success", f"Failed for zone {zone}: {res}"
        assert res.get("athlete_id") == "[ATHLETE_ath_5a77195e]"
        assert res.get("name") == "[ATHLETE_ath_5a77195e]"
        assert "aligned_stroke_rate" in res
        assert "aligned_l4_split" in res
        assert "prescription_reminder_fr" in res
        assert "prescription_reminder_en" in res

        leaks = find_pii_leaks(res, banned)
        assert not leaks, f"PII leaks detected in align_session_pace_to_l4 for zone {zone}: {leaks}"


def test_align_session_pace_to_l4_unsupported_zones():
    """Verify proper error response when unsupported zones are requested."""
    for bad_zone in ["AN", "TR", "VO2MAX", "random", ""]:
        res = align_session_pace_to_l4("[ATHLETE_ath_5a77195e]", bad_zone)
        assert res.get("status") == "error"
        assert "Unsupported intensity zone" in res.get("message", "")


def test_align_session_pace_to_l4_all_16_athletes(roster_ground_truth):
    """Stress test: align all 16 athletes across UT2, UT1, and AT, asserting zero PII leaks."""
    banned = set(roster_ground_truth["raw_ids"] + roster_ground_truth["names"])

    for a in roster_ground_truth["athletes"]:
        token = f"[ATHLETE_{a['pseudonym']}]"
        for zone in ["UT2", "UT1", "AT"]:
            res = align_session_pace_to_l4(token, zone)
            assert res.get("status") == "success"
            assert res.get("athlete_id") == token
            assert res.get("name") == token

            leaks = find_pii_leaks(res, banned)
            assert not leaks, f"PII leaks for athlete {token} in zone {zone}: {leaks}"


# ==============================================================================
# 5. UI Theme Classes & Canvas Mask Stress Tests
# ==============================================================================

def test_ui_theme_main_canvas_mask_not_unqualified_dark():
    """Assert that <main> no longer has an unqualified bg-darkbg canvas mask."""
    # Find the <main> tag in HTML_PAGE
    main_match = re.search(r"<main[^>]*>", HTML_PAGE)
    assert main_match is not None, "<main> tag not found in HTML_PAGE"
    main_tag = main_match.group(0)

    # Must contain paired responsive class bg-slate-100 dark:bg-darkbg
    assert "bg-slate-100" in main_tag, f"<main> missing light background class: {main_tag}"
    assert "dark:bg-darkbg" in main_tag, f"<main> missing dark background class: {main_tag}"
    # Assert no unqualified ' bg-darkbg' class
    assert not re.search(r'(?<!dark:)bg-darkbg', main_tag), f"<main> has unqualified bg-darkbg: {main_tag}"


def test_ui_theme_key_containers_have_paired_dark_classes():
    """Verify that all key container elements have paired dark: responsive classes."""
    containers = [
        # (pattern, required_light, required_dark, description)
        (r"<body[^>]*>", "bg-slate-100", "dark:bg-darkbg", "body container"),
        (r"<header[^>]*>", "bg-white/80", "dark:bg-darkpanel/80", "top header"),
        (r"<aside class=\"w-72[^>]*>", "bg-slate-50/80", "dark:bg-darkpanel/50", "column 1 sidebar"),
        (r"<aside class=\"w-80[^>]*>", "bg-slate-50/80", "dark:bg-darkpanel/70", "column 3 sidebar"),
        (r"<input[^>]*id=\"user-input\"[^>]*>", "bg-white", "dark:bg-darkbg", "chat input"),
    ]

    for pattern, light_cls, dark_cls, desc in containers:
        match = re.search(pattern, HTML_PAGE)
        assert match is not None, f"Tag for {desc} not found in HTML_PAGE"
        tag = match.group(0)
        assert light_cls in tag, f"{desc} missing light class '{light_cls}': {tag}"
        assert dark_cls in tag, f"{desc} missing dark class '{dark_cls}': {tag}"


def test_ui_theme_toggle_elements_and_persistence_script():
    """Verify that the theme toggle button, icons, and JS handler exist and function."""
    assert 'id="theme-toggle-btn"' in HTML_PAGE
    assert 'id="theme-icon"' in HTML_PAGE
    assert 'id="theme-text"' in HTML_PAGE
    assert 'localStorage.getItem(\'rowing-coach-theme\')' in HTML_PAGE
    assert 'localStorage.setItem(\'rowing-coach-theme\', theme)' in HTML_PAGE
    assert 'document.documentElement.classList.remove(\'dark\')' in HTML_PAGE
    assert 'document.documentElement.classList.add(\'dark\')' in HTML_PAGE
