"""Portfolion laskenta, omaisuusryhmittely ja fiat-arvostus."""

from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from ..db.database import Database
from ..db.models import utc_now_iso


class AssetHolding(BaseModel):
    asset_id: str
    symbol: str
    name: Optional[str]
    amount: str
    price_eur: Optional[str] = None
    value_eur: Optional[str] = None
    price_usd: Optional[str] = None
    value_usd: Optional[str] = None
    venue: str
    account_name: str
    kind: str


class GroupHolding(BaseModel):
    group_name: str
    total_amount: str
    value_eur: Optional[str] = None
    value_usd: Optional[str] = None
    items: List[AssetHolding] = Field(default_factory=list)


class SourceHolding(BaseModel):
    account_id: str
    account_name: str
    venue: str
    type: str  # 'cex' tai 'onchain'
    value_eur: Optional[str] = None
    value_usd: Optional[str] = None
    status: str
    last_synced_at: Optional[str] = None


class PortfolioSummary(BaseModel):
    as_of: str
    total_eur: str
    total_usd: str
    groups: List[GroupHolding]
    sources: List[SourceHolding]
    warnings: List[str] = Field(default_factory=list)


class PortfolioAggregator:
    def __init__(self, db: Database):
        self.db = db

    def get_portfolio(self) -> PortfolioSummary:
        as_of = utc_now_iso()
        warnings: List[str] = []

        # 1. Tarkistetaan tilien virheet ja varoitukset
        accounts = self.db.fetch_all("SELECT * FROM accounts WHERE is_active = 1;")
        credentials = self.db.fetch_all("SELECT * FROM cex_credentials;")
        cred_map = {c["account_id"]: c for c in credentials}

        sources: List[SourceHolding] = []
        for acc in accounts:
            acc_id = acc["id"]
            c_info = cred_map.get(acc_id)

            status = "ok"
            if acc["last_error_type"] and acc["last_error_type"] != "ok":
                status = acc["last_error_type"]
                warnings.append(f"Yhteysongelma tilillä '{acc['name']}': {acc['last_error_msg'] or status}")

            if c_info:
                if c_info["can_trade"] or c_info["can_withdraw"]:
                    warnings.append(f"Tilin '{acc['name']}' CEX-avaimella on kaupankäynti- tai nosto-oikeus! (Vaara)")
                if c_info["expires_at"]:
                    # tarkistetaan päivät
                    from datetime import datetime, timezone
                    try:
                        exp_dt = datetime.fromisoformat(c_info["expires_at"])
                        days_left = (exp_dt - datetime.now(timezone.utc)).total_days()
                        if days_left <= 14:
                            warnings.append(f"Tilin '{acc['name']}' API-avain vanhenee {int(days_left)} pv kuluessa!")
                    except Exception:
                        pass

            sources.append(SourceHolding(
                account_id=acc_id,
                account_name=acc["name"],
                venue=acc["venue"],
                type=acc["type"],
                value_eur="0",
                value_usd="0",
                status=status,
                last_synced_at=acc["last_success_at"]
            ))

        # 2. Haetaan kaikki voimassa olevat saldot ja kurssit
        balance_rows = self.db.fetch_all(
            """
            SELECT 
                b.id as balance_id,
                b.account_id,
                b.kind,
                b.amount,
                a.id as asset_id,
                a.symbol,
                a.name as asset_name,
                a.display_group,
                acc.name as account_name,
                acc.venue,
                p_eur.price as price_eur,
                p_usd.price as price_usd
            FROM balances b
            JOIN accounts acc ON b.account_id = acc.id
            JOIN assets a ON b.asset_id = a.id
            LEFT JOIN prices p_eur ON a.id = p_eur.asset_id AND p_eur.currency = 'EUR'
            LEFT JOIN prices p_usd ON a.id = p_usd.asset_id AND p_usd.currency = 'USD'
            WHERE acc.is_active = 1 AND CAST(b.amount AS NUMERIC) > 0;
            """
        )

        groups_map: Dict[str, List[AssetHolding]] = {}
        source_eur_map: Dict[str, Decimal] = {s.account_id: Decimal("0") for s in sources}
        source_usd_map: Dict[str, Decimal] = {s.account_id: Decimal("0") for s in sources}
        total_eur = Decimal("0")
        total_usd = Decimal("0")

        missing_prices: set = set()

        for row in balance_rows:
            group_key = row["display_group"] or row["symbol"]
            amount_dec = Decimal(str(row["amount"]))

            p_eur_str = row["price_eur"]
            p_usd_str = row["price_usd"]

            val_eur_str = None
            val_usd_str = None

            if p_eur_str:
                v_eur = amount_dec * Decimal(str(p_eur_str))
                val_eur_str = f"{v_eur:.2f}"
                total_eur += v_eur
                source_eur_map[row["account_id"]] = source_eur_map.get(row["account_id"], Decimal("0")) + v_eur
            else:
                missing_prices.add(row["symbol"])

            if p_usd_str:
                v_usd = amount_dec * Decimal(str(p_usd_str))
                val_usd_str = f"{v_usd:.2f}"
                total_usd += v_usd
                source_usd_map[row["account_id"]] = source_usd_map.get(row["account_id"], Decimal("0")) + v_usd

            holding = AssetHolding(
                asset_id=row["asset_id"],
                symbol=row["symbol"],
                name=row["asset_name"],
                amount=row["amount"],
                price_eur=p_eur_str,
                value_eur=val_eur_str,
                price_usd=p_usd_str,
                value_usd=val_usd_str,
                venue=row["venue"],
                account_name=row["account_name"],
                kind=row["kind"]
            )

            if group_key not in groups_map:
                groups_map[group_key] = []
            groups_map[group_key].append(holding)

        if missing_prices:
            warnings.append(f"Kursseja puuttuu tokeneilta: {', '.join(sorted(missing_prices))}. Niiden arvoksi on merkitty tyhjä.")

        # Muodostetaan ryhmäkoosteet
        group_holdings: List[GroupHolding] = []
        for g_name, items in sorted(groups_map.items()):
            g_amount = sum(Decimal(it.amount) for it in items)
            g_eur = sum(Decimal(it.value_eur) for it in items if it.value_eur)
            g_usd = sum(Decimal(it.value_usd) for it in items if it.value_usd)
            
            group_holdings.append(GroupHolding(
                group_name=g_name,
                total_amount=f"{g_amount:f}",
                value_eur=f"{g_eur:.2f}" if g_eur > 0 else None,
                value_usd=f"{g_usd:.2f}" if g_usd > 0 else None,
                items=items
            ))

        # Päivitetään lähteiden summat
        for s in sources:
            eur_val = source_eur_map.get(s.account_id, Decimal("0"))
            usd_val = source_usd_map.get(s.account_id, Decimal("0"))
            s.value_eur = f"{eur_val:.2f}"
            s.value_usd = f"{usd_val:.2f}"

        return PortfolioSummary(
            as_of=as_of,
            total_eur=f"{total_eur:.2f}",
            total_usd=f"{total_usd:.2f}",
            groups=group_holdings,
            sources=sources,
            warnings=warnings
        )
