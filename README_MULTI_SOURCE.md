# Small Cap Radar V1.1 — Multi-source

Cloud-first Streamlit scanner. No automatic order submission.

## Data architecture
- Massive: broad US snapshot / optional 1-minute bars.
- Alpaca: paper/live authentication, movers, most-active, IEX snapshots and 1-minute bars.
- FMP: screener and company profiles.
- Finnhub: quote, profile, company news, news sentiment.
- Twelve Data: secondary quote validation.
- SEC EDGAR: filings and dilution-risk flags.
- Nasdaq Trader: current trade-halt RSS.
- FINRA: Reg SHO daily short-sale volume as context only.
- GlobeNewswire + PR Newswire: catalyst RSS.
- ClinicalTrials.gov + openFDA: biotech context when a company profile identifies a likely life-sciences issuer.
- Finviz / TradingView: CSV export ingestion in Streamlit; no scraping of private/internal endpoints.

## Streamlit Secrets
Use `.streamlit/secrets.toml.example` as the template. Never commit real keys.

## Design principle
The bot does not treat any single free feed as consolidated truth. It records which sources contributed to each candidate and separates `score` from `data_confidence`.
