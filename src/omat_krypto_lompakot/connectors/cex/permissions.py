"""CEX API-avainten oikeuksien ja tilojen analyysi."""

from typing import List, Optional
from pydantic import BaseModel, Field


class CexPermissionResult(BaseModel):
    is_valid: bool
    status_color: str = "green"  # 'green', 'yellow', 'red'
    can_read: bool = True
    can_trade: bool = False
    can_withdraw: bool = False
    expires_at: Optional[str] = None
    expiry_source: Optional[str] = None
    error_type: Optional[str] = "ok"  # 'ok', 'auth_failed', 'rate_limited', 'network', 'invalid_permissions'
    error_message: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)

    def evaluate_status_color(self) -> str:
        """Laskee väristatuksen suunnitelman sääntöjen mukaisesti:
        - Vihreä: viimeisin kutsu ok, oikeudet ovat vain luku, ja expires_at puuttuu tai > 14 pv
        - Keltainen: expires_at <= 14 pv tai tilapäinen häiriö (rate limit / verkko)
        - Punainen: auth_failed, vanhentunut, tai avaimella on trade- tai withdraw-oikeus!
        """
        if self.can_trade or self.can_withdraw:
            self.warnings.append("VAARA: API-avaimella on kaupankäynti- tai nosto-oikeus! Suositellaan vaihtoa puhtaaseen lukuoikeusavaimeen.")
            self.status_color = "red"
            return "red"

        if not self.is_valid:
            if self.error_type in ("rate_limited", "network"):
                self.status_color = "yellow"
                return "yellow"
            self.status_color = "red"
            return "red"

        # Tarkistetaan vanheneminen jos asetettu
        if self.expires_at:
            from datetime import datetime, timezone
            try:
                exp_dt = datetime.fromisoformat(self.expires_at)
                now_dt = datetime.now(timezone.utc)
                days_left = (exp_dt - now_dt).total_days()
                if days_left <= 0:
                    self.warnings.append("API-avain on vanhentunut.")
                    self.status_color = "red"
                    return "red"
                elif days_left <= 14:
                    self.warnings.append(f"API-avain vanhenee pian ({int(days_left)} pv jäljellä).")
                    self.status_color = "yellow"
                    return "yellow"
            except Exception:
                pass

        self.status_color = "green"
        return "green"
