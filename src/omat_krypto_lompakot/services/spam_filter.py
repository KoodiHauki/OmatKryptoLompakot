"""Roskatransaktioiden ja -tokenien tunnistuslogiikka (Spam / Dust Filter)."""

import logging
from decimal import Decimal
from typing import Optional, Tuple
from ..db.database import Database

logger = logging.getLogger(__name__)


class SpamFilter:
    def __init__(self, db: Database):
        self.db = db

    def evaluate_transaction(
        self,
        chain: str,
        kind: str,
        amount: Optional[str],
        contract_address: Optional[str] = None,
        has_price: bool = True
    ) -> Tuple[bool, Optional[str]]:
        """Määrittää onko transaktio roskatransaktio (is_spam, reason).
        
        Säännöt:
        1. Käyttäjän token_overrides voittaa aina.
        2. Approval-transaktiot ovat turvallisuuskriittisiä eivätkä KOSKAAN roskaliputettuja.
        3. Nolla-arvoiset mainossiirrot merkitään roskaksi.
        4. Tuntemattomat token-sopimukset, joilla ei ole kurssia/arvoa, merkitään roskaksi.
        """
        # 1. Tarkistetaan käyttäjän oma override
        if contract_address:
            clean_addr = contract_address.lower().strip()
            override = self.db.fetch_one(
                "SELECT action, notes FROM token_overrides WHERE chain = ? AND lower(contract_address) = ?;",
                (chain, clean_addr)
            )
            if override:
                if override["action"] == "allow":
                    return False, None
                elif override["action"] == "deny":
                    return True, override["notes"] or "Käyttäjän estämä sopimus (token_override)"

        # 2. Approvalit eivät ole roskaliputettuja
        if kind == "approval":
            return False, None

        # 3. Nolla-arvoiset mainos- / kalastelusiirrot
        if amount is not None:
            try:
                dec_val = Decimal(str(amount))
                if dec_val == 0 and kind in ("transfer", "deposit", "other"):
                    return True, "Nolla-arvoinen mainossiirto (0-value dust)"
            except Exception:
                pass

        # 4. Tuntematon sopimus ilman hintanoteerausta
        if contract_address and not has_price:
            return True, "Tuntematon token ilman kurssia (mahdollinen scam/dust airdrop)"

        return False, None
