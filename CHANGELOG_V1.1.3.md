# Small Cap Radar V1.1.3 — Scalp Score Weights + Moomoo Import

## Scoring

- The default Classic engine is profile-aware. In `EXIT_MODE = "scalping"`, its maximum score components are Gap 3.5/10, RVOL 3.5/10, low float 1.5/10, and RSI 1.5/10. Gap plus RVOL therefore represent 70% of the maximum score. The ordinary-float tier contributes 0.8 instead of 1.5, so the total remains bounded at 10.
- In `EXIT_MODE = "swing"`, the prior Classic maximums are preserved: Gap 3, RVOL 3, low-float 2.5, RSI 1.5.
- The Gap component is capped; increasingly extreme gaps do not add points without limit. Existing price/float/dilution, halt, chase, Entry, data-confidence, session, and approval-threshold checks remain in place.
- Smart engine weights now allocate 3.6 points to Gap, 3.5 to session volume relative to float, 0.9 to catalyst, and 2 points combined to market-cap band, float-turnover ratio, and price structure. Maximum total remains 10; the existing 9.0 approval threshold remains unchanged.
- The default Classic engine now receives Moomoo's session-specific `Pre Mkt Vol`/`After Hours Volume` or generic `Volume`. A positive imported `Vol Ratio` remains primary; if it is zero/missing, session volume divided by float supplies the volume points. If float is unavailable, the code falls back to a usable live RVOL or awards no fabricated volume points.
- Classic and Smart results expose their component values. The ranking table now displays `Pts Gap` and `Pts Vol.` beside the source metrics.

## Moomoo import

- Retains the column-name-based CSV/XLSX/XLS parser and regular, premarket, and after-hours aliases; column order does not determine metric mapping.
- Moomoo's percent Gap remains distinct from absolute-dollar `Chg`. The imported Gap has priority over a mismatched quote-feed calculation.
- Session-specific volume is used when exported; otherwise the generic `Volume` field is retained and passed as the supported fallback. Missing session-specific values are not fabricated.
- The uploaded regular-session export was parsed as 16/16 rows; the premarket export as 13/13 rows. The premarket sample did not contain a dedicated premarket-volume column, so it correctly supplied generic `Volume` instead.

## Verification and source

- `python3 -m compileall -q .` passed.
- `python3 -m unittest discover -s tests -v` passed: 10 tests, including the original Moomoo parser regressions and new no-network tests for both scoring engines.
- Based on public GitHub repository `https://github.com/Jorgeantonioeinar/SmallCap-Radar-V1`, commit `381c4dae7713441fa9f839ebef306764c8ccb4d7`; this package contains local changes only and was not pushed to GitHub.

## Use note

The score is a candidate-ranking heuristic, not a guarantee or a standalone buy signal. Paper-test the new distribution and review fills, spreads, slippage, halts, and false breakouts before considering live use.
