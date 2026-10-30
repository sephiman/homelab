#!/usr/bin/env bash
# Hermes cron pre-run script for the Shared Ledger holdings report: values one household's crypto and
# ETF/stock holdings at today and at yesterday (net quantity of BUY − SELL lots traded up to that day ×
# latest price up to that day × FX into the household currency, as the app does). Snapshots aren't used:
# Casa takes them monthly. Nothing held on either day prints nothing, and Hermes then skips the model.
set -euo pipefail

readonly HOUSEHOLD=casa
readonly TIMEZONE=Europe/Madrid
# Crypto prices refresh hourly, ETF/stock prices once a night on business days.
readonly CRYPTO_STALE_HOURS=3
readonly EQUITY_STALE_DAYS=4

# Rows: `household-missing`, `held <count>`, `block <crypto|etf-stocks|total> <today> <yesterday>`,
# `unpriced <symbol>` (left out of the totals), `stale <symbol> <price date or time>`.
holdings_valuation() {
  psql "service=homelab dbname=sharedledger" -X -q -A -t -F $'\t' -v ON_ERROR_STOP=1 \
    -v household="$HOUSEHOLD" -v today="$(TZ="$TIMEZONE" date +%F)" \
    -v yesterday="$(TZ="$TIMEZONE" date -d yesterday +%F)" \
    -v crypto_stale_hours="$CRYPTO_STALE_HOURS" -v equity_stale_days="$EQUITY_STALE_DAYS" <<'SQL'
WITH household AS (
    SELECT id, currency FROM households WHERE lower(name) = lower(:'household')
),
days(label, day) AS (
    VALUES ('today', :'today'::date), ('yesterday', :'yesterday'::date)
),
positions AS (
    SELECT d.label, d.day, h.asset_class, h.symbol, h.native_currency, h.provider, h.provider_symbol,
           sum(CASE l.type WHEN 'BUY' THEN l.quantity ELSE -l.quantity END) AS quantity
    FROM days d
    CROSS JOIN household hh
    JOIN holdings h ON h.household_id = hh.id AND h.deleted_at IS NULL AND h.active
                   AND h.asset_class IN ('crypto', 'etf', 'stock')
    JOIN holding_lots l ON l.holding_id = h.id AND l.deleted_at IS NULL AND l.traded_on <= d.day
    GROUP BY d.label, d.day, h.id
    HAVING sum(CASE l.type WHEN 'BUY' THEN l.quantity ELSE -l.quantity END) <> 0
),
valued AS (
    SELECT p.label, p.day, p.asset_class, p.symbol, pr.price_date, pr.as_of,
           p.quantity * pr.price * CASE WHEN pr.currency = hh.currency THEN 1 ELSE fx.rate END AS value
    FROM positions p
    CROSS JOIN household hh
    -- The app asks the provider for crypto directly in the base currency, for the rest in its own.
    LEFT JOIN LATERAL (
        SELECT ph.price, ph.price_date, ph.as_of, ph.currency
        FROM price_history ph
        WHERE ph.provider = p.provider AND ph.provider_symbol = p.provider_symbol
          AND ph.currency = CASE WHEN p.asset_class = 'crypto' THEN hh.currency ELSE p.native_currency END
          AND ph.price_date <= p.day
        ORDER BY ph.price_date DESC, ph.as_of DESC
        LIMIT 1
    ) pr ON true
    -- No FX rate leaves the value null (unpriced), never silently at a rate of 1.
    LEFT JOIN LATERAL (
        SELECT r.rate
        FROM fx_rates r
        WHERE r.base_currency = pr.currency AND r.quote_currency = hh.currency AND r.rate_date <= p.day
        ORDER BY r.rate_date DESC
        LIMIT 1
    ) fx ON true
),
blocks AS (
    SELECT coalesce(CASE WHEN asset_class = 'crypto' THEN 'crypto' ELSE 'etf-stocks' END, 'total') AS block,
           sum(value) FILTER (WHERE label = 'today') AS today,
           sum(value) FILTER (WHERE label = 'yesterday') AS yesterday
    FROM valued
    GROUP BY ROLLUP (CASE WHEN asset_class = 'crypto' THEN 'crypto' ELSE 'etf-stocks' END)
)
SELECT 'household-missing', NULL, NULL, NULL WHERE NOT EXISTS (SELECT 1 FROM household)
UNION ALL
SELECT 'held', count(*)::text, NULL, NULL FROM positions
UNION ALL
SELECT 'block', block, round(coalesce(today, 0), 2)::text, round(coalesce(yesterday, 0), 2)::text FROM blocks
UNION ALL
SELECT 'unpriced', symbol, NULL, NULL FROM valued WHERE label = 'today' AND value IS NULL
UNION ALL
SELECT 'stale', symbol, CASE WHEN asset_class = 'crypto' THEN to_char(as_of AT TIME ZONE :'timezone', 'YYYY-MM-DD HH24:MI')
                             ELSE price_date::text END, NULL
FROM valued
WHERE label = 'today' AND value IS NOT NULL
  AND CASE WHEN asset_class = 'crypto' THEN as_of < now() - make_interval(hours => :crypto_stale_hours)
           ELSE price_date < day - :equity_stale_days END;
SQL
}

format_report() {
  awk -F'\t' -v household="$HOUSEHOLD" -v now="$(TZ="$TIMEZONE" date '+%Y-%m-%d %H:%M %Z')" '
    $1 == "household-missing" { missing = 1 }
    $1 == "held" { held = $2 }
    $1 == "block" { today[$2] = $3; yesterday[$2] = $4 }
    $1 == "unpriced" { warnings = warnings sprintf("%s has no price or FX rate: left out of the totals\n", $2) }
    $1 == "stale" { warnings = warnings sprintf("%s price is stale (from %s)\n", $2, $3) }
    END {
      if (missing) { printf "Shared Ledger has no household named %s: the holdings report cannot run.\n", household; exit }
      if (held == 0) exit
      printf "=== Shared Ledger holdings of %s, now (%s) vs yesterday, in the household currency\n", household, now
      n = split("crypto etf-stocks total", blocks, " ")
      for (i = 1; i <= n; i++) {
        b = blocks[i]
        change = today[b] - yesterday[b]
        pct = (yesterday[b] != 0) ? sprintf(" (%+.2f%%)", 100 * change / yesterday[b]) : ""
        printf "%s\ttoday %.2f\tyesterday %.2f\tchange %+.2f%s\n", b, today[b], yesterday[b], change, pct
      }
      if (warnings != "") printf "=== warnings\n%s", warnings
    }
  '
}

if ! valuation="$(holdings_valuation 2>&1)"; then
  printf '=== Shared Ledger holdings of %s: the query failed\n%s\n' "$HOUSEHOLD" "$valuation"
  exit 0
fi
format_report <<<"$valuation"
