# S-1 Prospector

Automated pipeline for identifying potential LP prospects from SEC S-1 filings (IPOs). Scans recent filings, extracts principal stockholders, enriches with foundation data, and matches against your Affinity CRM.

## What It Does

1. **Fetches recent S-1 filings** from SEC EDGAR (last 7 days by default)
2. **Parses stockholder tables** to extract investor names, ownership percentages, and share counts
3. **Classifies entities** (foundation, family office, fund, corporate, etc.)
4. **Enriches foundations** with officer data from ProPublica 990 filings
5. **Matches against Affinity** to flag existing contacts and pull opportunity status
6. **Outputs results** to Google Sheets or CSV with LinkedIn search URLs

## Output

Each run produces a table like:

| Investor Name | IPO Company | Filing Date | Ownership % | Entity Type | In CRM? | CRM Status | Foundation Contacts | LinkedIn Search |
|---------------|-------------|-------------|-------------|-------------|---------|------------|---------------------|-----------------|
| Smith Family Office | Acme Energy | 2026-01-28 | 4.2% | family_office | Yes | Active | — | [Link] |
| Greenfield Foundation | Acme Energy | 2026-01-28 | 2.8% | foundation | No | — | John Green (Trustee) | [Link] |

## Setup

### 1. Clone and Install

```bash
git clone https://github.com/yourorg/s1-prospector.git
cd s1-prospector
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

**Required:**
- `AFFINITY_API_KEY` - Get from Affinity Settings > API
- `AFFINITY_LIST_NAME` - Name of your fundraising list in Affinity (default: "Fundraising")

**Optional (for Google Sheets output):**
- `GOOGLE_SHEET_ID` - ID from your Google Sheet URL
- `GOOGLE_CREDENTIALS_PATH` - Path to service account JSON

### 3. Affinity Setup

Your Affinity fundraising list should have:
- Opportunities with organizations and/or persons tagged
- A status or stage field to track pipeline progress
- Notes field (optional but recommended)

The script will match S-1 investor names against organization names and person names in this list.

### 4. Google Sheets Setup (Optional)

If you want output to Google Sheets:

1. Create a Google Cloud project and enable Sheets API
2. Create a service account and download the JSON key
3. Create a Google Sheet and share it with the service account email
4. Set `GOOGLE_SHEET_ID` and `GOOGLE_CREDENTIALS_PATH` in `.env`

If not configured, results will output to CSV in the project directory.

## Running Locally

```bash
python main.py
```

Output goes to:
- `s1_investors_YYYY-MM-DD.csv` (if using CSV output)
- New worksheet in your Google Sheet (if configured)

## Deploying to Railway

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/yourorg/s1-prospector.git
git push -u origin main
```

### 2. Create Railway Project

1. Go to [railway.app](https://railway.app) and create new project
2. Connect your GitHub repo
3. Add environment variables in Railway dashboard (same as `.env`)

### 3. Set Up Cron Schedule

Railway supports cron jobs. Add a cron trigger in your project settings:

```
0 9 * * 1
```

This runs every Monday at 9 AM UTC. Adjust to your preference.

Alternatively, use Railway's scheduled deployments or trigger via webhook.

### 4. (Optional) Add Email Notifications

To get emailed when the job runs, you could:
- Add SendGrid/Mailgun integration
- Use a simple SMTP setup
- Connect to Slack via webhook

The `output.py` file has a `format_for_email()` function ready for this.

## Customization

### Entity Classification

Edit `classify_entity()` in `main.py` to adjust how investor types are categorized. Current heuristics look for keywords like "foundation," "family office," "capital," etc.

### Matching Threshold

The Affinity fuzzy matching threshold defaults to 80 (out of 100). Adjust in `affinity.py` `find_match()` if you're getting too many/few matches.

### SEC User Agent

Update the `SEC_HEADERS` in `edgar.py` with your contact email. SEC requires this for API compliance.

## Limitations

- **S-1 parsing is imperfect** - stockholder table formats vary between filings. The parser handles common formats but may miss some or extract incomplete data.
- **Foundation officer lookup is limited** - ProPublica's API doesn't expose full officer lists directly. For complete data, you'd need to parse the actual 990 PDFs.
- **Acquisition data not included** - This only covers IPO S-1s. Merger proxies (DEF 14A) have a different structure and would need separate parsing logic.

## Troubleshooting

**No S-1s found**: Check the `DAYS_BACK` setting. If it's a slow IPO week, there may genuinely be none.

**Affinity matching not working**: Verify your API key has read access to the fundraising list. Check that `AFFINITY_LIST_NAME` matches exactly.

**Google Sheets auth failing**: Ensure the service account email has Editor access to the sheet.

## License

MIT
