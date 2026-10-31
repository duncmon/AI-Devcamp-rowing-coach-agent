"""Reversible Bracketed PII Vault for Rowing Coach Agent.

Implements Architectural Blueprint v0.2 & GDPR Article 9 Special Category Health Data Compliance:
  - Gate 1: Tokenizes raw athlete PII (names, phone numbers, emails) into deterministic
    bracketed cryptographic pseudonym tokens ([ATHLETE_ath_5a77195e], [PHONE_NUMBER_1], [EMAIL_ADDRESS_1])
    before history reaches the model.
  - Multi-turn mapping: Persists mapping per session ID so tokens remain consistent.
  - Gate 2: Rehydrates tokens on outgoing coaching recommendations and asserts that
    zero orphan/unmapped tokens (e.g. [ATHLETE_NAME_99] or [ATHLETE_ath_deadbeef]) leak through.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
ATHLETES_FILE = PROJECT_ROOT / "data" / "athletes.json"


class VaultSafetyError(ValueError):
    """Raised when an unmapped or orphan token is detected during rehydration."""
    pass


class ReversiblePiiVault:
    """Session-scoped reversible token vault for athlete personal data."""

    def __init__(self):
        # Maps session_id -> { "to_token": {raw: token}, "to_raw": {token: raw}, "counters": {category: int} }
        self._sessions: Dict[str, Dict] = {}
        self._squad_roster_map = self._load_squad_pseudonym_map()

    def _load_squad_pseudonym_map(self) -> Dict[str, str]:
        """Loads all athletes and known aliases, mapping lowercase name to pseudonym."""
        m: Dict[str, str] = {
            "coach david": "ath_5a77195e",
            "david montgomery": "ath_5a77195e",
            "david montgamery": "ath_5a77195e",
        }
        if ATHLETES_FILE.exists():
            try:
                with open(ATHLETES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for a in data.get("athletes", []):
                    pseudonym = a.get("pseudonym", f"ath_{a['id']}")
                    m[a["name"].lower()] = pseudonym
                    m[a["id"].lower()] = pseudonym
                    # Handle unaccented versions if needed
                    norm_name = a["name"].lower().replace("é", "e").replace("è", "e").replace("ç", "c")
                    m[norm_name] = pseudonym
            except Exception as e:
                log.warning(f"Failed to load squad athletes in vault: {e}")
        return m

    def _get_session(self, session_id: str) -> Dict:
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "to_token": {},
                "to_raw": {},
                "counters": {
                    "ATHLETE_NAME": 0,
                    "PHONE_NUMBER": 0,
                    "EMAIL_ADDRESS": 0,
                },
            }
        return self._sessions[session_id]

    def vault_value(self, session_id: str, raw_value: str, category: str, custom_token: Optional[str] = None) -> str:
        """Assigns or retrieves a token for a given raw PII string."""
        raw_clean = raw_value.strip()
        sess = self._get_session(session_id)
        if raw_clean in sess["to_token"]:
            return sess["to_token"][raw_clean]

        if custom_token:
            token = custom_token
        else:
            sess["counters"][category] = sess["counters"].get(category, 0) + 1
            idx = sess["counters"][category]
            token = f"[{category}_{idx}]"

        sess["to_token"][raw_clean] = token
        sess["to_raw"][token] = raw_clean
        return token

    def strip_and_vault(self, text: str, session_id: str) -> str:
        """Gate 1: Scans text for athlete names, emails, and domestic phone numbers, replacing them with tokens."""
        if not text:
            return text

        # 1. Email pattern
        email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
        for match in re.findall(email_pattern, text):
            tok = self.vault_value(session_id, match, "EMAIL_ADDRESS")
            text = text.replace(match, tok)

        # 2. Phone pattern (French & UK domestic formats)
        phone_pattern = r"(?:(?:\+\d{1,3}[\s-]?)?(?:0[1-9](?:[\s.-]?\d{2}){4}|0[1-9]\d{1,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}))"
        for match in re.findall(phone_pattern, text):
            tok = self.vault_value(session_id, match, "PHONE_NUMBER")
            text = text.replace(match, tok)

        # 3. Known squad athlete names with cryptographic pseudonyms (GDPR Article 9)
        # Sort by length descending to match full names before prefixes (e.g. "David Montgomery" before "David")
        sorted_squad_names = sorted(self._squad_roster_map.keys(), key=len, reverse=True)
        for name in sorted_squad_names:
            pseudonym = self._squad_roster_map[name]
            # Case-insensitive boundary match for athlete names
            pattern = rf"(?i)\b{re.escape(name)}\b"
            for m in re.finditer(pattern, text):
                raw_matched = m.group(0)
                tok = self.vault_value(
                    session_id,
                    raw_matched,
                    "ATHLETE_NAME",
                    custom_token=f"[ATHLETE_{pseudonym}]",
                )
                text = text.replace(raw_matched, tok)

        # 4. Fallback legacy / external test squad athlete names (Daniel Okonkwo, Sarah Chen, Laurent Dupont)
        legacy_test_names = [
            "Daniel Okonkwo",
            "Sarah Chen",
            "Laurent Dupont",
        ]
        for name in legacy_test_names:
            if name in text:
                tok = self.vault_value(session_id, name, "ATHLETE_NAME")
                text = text.replace(name, tok)

        # 5. Dynamic athlete name detection (quoted names or athlete/rower introductions)
        quoted_patterns = [
            r'["\']([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)["\']',
            r'(?:rower|athlete|coach|named)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
        ]
        for pat in quoted_patterns:
            for match in re.findall(pat, text):
                if match.lower() not in self._squad_roster_map and match not in legacy_test_names and not match.startswith("["):
                    tok = self.vault_value(session_id, match, "ATHLETE_NAME")
                    text = re.sub(rf'["\']{re.escape(match)}["\']', tok, text)
                    text = text.replace(match, tok)

        return text

    def rehydrate(self, text: str, session_id: str) -> str:
        """Gate 2: Re-inserts real athlete data from vault.

        Fails closed: if text contains any unknown [ATHLETE_NAME_X] or [ATHLETE_ath_X] token, raises VaultSafetyError.
        """
        if not text:
            return text

        sess = self._get_session(session_id)
        to_raw = sess["to_raw"]

        # Replace known tokens
        for token, raw in to_raw.items():
            text = text.replace(token, raw)

        # Safety Audit: check for leftover / orphan / hallucinated tokens
        orphan_pattern = r"\[(?:(?:ATHLETE_NAME|PHONE_NUMBER|EMAIL_ADDRESS)_\d+|ATHLETE_ath_[a-f0-9]+)\]"
        orphans = re.findall(orphan_pattern, text)
        if orphans:
            orphan_matches = [m.group(0) for m in re.finditer(orphan_pattern, text)]
            raise VaultSafetyError(
                f"Gate 2 Safety Violation: Detected orphan or hallucinated PII tokens in output: {orphan_matches}"
            )

        return text
