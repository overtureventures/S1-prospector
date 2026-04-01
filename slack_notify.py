"""
Slack notification module for the S-1 Prospector.
Builds and sends the weekly LP prospects message to #fundraising-bot.
"""

import os
import logging
import requests
from datetime import datetime
from typing import List, Dict

logger = logging.getLogger(__name__)

FUNDRAISING_BOT_CHANNEL = 'C0AQHP58A0Z'  # #fundraising-bot


def _post_to_slack(channel_id: str, message: str) -> bool:
    """Post a message to Slack via the Web API."""
    # Read token at call time, not module load time, so Railway env vars are present
    token = os.getenv('SLACK_BOT_TOKEN', '').strip()
    logger.info(f'SLACK_BOT_TOKEN present: {bool(token)} length: {len(token)}')
    if not token:
        logger.warning('SLACK_BOT_TOKEN not set. Skipping Slack notification.')
        return False

    try:
        r = requests.post(
            'https://slack.com/api/chat.postMessage',
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            },
            json={
                'channel': channel_id,
                'text': message,
                'unfurl_links': False,
                'unfurl_media': False,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if not data.get('ok'):
            logger.error(f'Slack API error: {data.get("error", "unknown")}')
            return False
        logger.info(f'Slack message sent to {channel_id}')
        return True
    except requests.RequestException as e:
        logger.error(f'Slack request failed: {e}')
        return False


def build_slack_message(
    qualified_investors: List[Dict],
    filing_descriptions: Dict[str, str],
    total_filings_scanned: int,
    total_filings_after_filter: int,
    run_date: str,
) -> str:
    """
    Build the weekly Slack message grouped by IPO company.

    Format:
        📋 *Weekly S-1 LP Prospects* | Apr 2, 2026
        23 filings scanned · 8 after sector filter · 14 qualified investors

        *Infleqtion, Inc.* — quantum computing company, IPO Mar 31
        • LCP Quantum Management LLC — institutional (13F verified)
        • Matthew Kinsella — individual

        *WaterBridge Infrastructure LLC* — midstream water infrastructure, IPO Mar 27
        • Horizon Kinetics Asset Management LLC — 5.5% (13F verified)
        • FMR LLC — 3.0% (13F verified)
        • Devon WB Holdco L.L.C. — 14.4%
    """
    date_fmt = datetime.strptime(run_date, '%Y-%m-%d').strftime('%b %-d, %Y')
    total_qualified = len(qualified_investors)

    lines = [
        f'📋 *Weekly S-1 LP Prospects* | {date_fmt}',
        f'_{total_filings_scanned} filings scanned · '
        f'{total_filings_after_filter} after sector filter · '
        f'{total_qualified} qualified investors_',
        '',
    ]

    if not qualified_investors:
        lines.append('_No qualified LP prospects found this week._')
        return '\n'.join(lines)

    # Group by IPO company
    by_company: Dict[str, list] = {}
    company_filing_dates: Dict[str, str] = {}
    for inv in qualified_investors:
        company = inv['company_ipo']
        by_company.setdefault(company, []).append(inv)
        company_filing_dates[company] = inv['filing_date']

    for company, investors in by_company.items():
        filing_date = company_filing_dates[company]
        try:
            date_display = datetime.strptime(filing_date, '%Y-%m-%d').strftime('%b %-d')
        except Exception:
            date_display = filing_date

        description = filing_descriptions.get(company, '')
        if description:
            # Lowercase first char for inline use after em dash
            description = description[0].lower() + description[1:] if description else ''
            header = f'*{company}* — {description}, IPO {date_display}'
        else:
            header = f'*{company}* — IPO {date_display}'

        lines.append(header)

        for inv in investors:
            name = inv['investor_name']
            investor_class = inv.get('investor_class', 'institutional')
            verified = inv.get('verified_13f', False)
            ownership = inv.get('ownership_pct', '')

            # Build the bullet line
            parts = [f'• {name}']

            detail_parts = []
            if ownership:
                detail_parts.append(f'{ownership}%')
            if investor_class == 'individual':
                detail_parts.append('individual')
            elif verified:
                detail_parts.append('13F verified')

            if detail_parts:
                parts.append(f'_({", ".join(detail_parts)})_')

            lines.append(' '.join(parts))

        lines.append('')

    return '\n'.join(lines).rstrip()


def send_weekly_report(
    qualified_investors: List[Dict],
    filing_descriptions: Dict[str, str],
    total_filings_scanned: int,
    total_filings_after_filter: int,
    run_date: str,
) -> bool:
    """Build and send the weekly LP prospects message to #fundraising-bot."""
    message = build_slack_message(
        qualified_investors=qualified_investors,
        filing_descriptions=filing_descriptions,
        total_filings_scanned=total_filings_scanned,
        total_filings_after_filter=total_filings_after_filter,
        run_date=run_date,
    )
    logger.info('Slack message preview:')
    for line in message.split('\n'):
        logger.info(f'  {line}')

    return _post_to_slack(FUNDRAISING_BOT_CHANNEL, message)
