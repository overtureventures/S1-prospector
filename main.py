"""
S-1 Prospector
Scans SEC EDGAR for recent S-1 filings, extracts principal stockholders,
filters to LP-qualified prospects, and posts to #fundraising-bot on Slack.
"""

import os
import logging
from datetime import datetime
from dotenv import load_dotenv

from edgar import get_recent_s1_filings, parse_stockholders
from filter import filter_filings, qualify_investors, get_company_description
from slack_notify import send_weekly_report
from output import write_to_csv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()


def classify_entity(name: str) -> str:
    name_lower = name.lower()
    if any(t in name_lower for t in ['foundation', 'endowment']):
        return 'foundation'
    if any(t in name_lower for t in ['family office', 'family trust', 'family lp']):
        return 'family_office'
    if any(t in name_lower for t in ['trust', 'estate']):
        return 'trust'
    if any(t in name_lower for t in ['capital', 'partners', 'ventures', 'fund',
                                      'management', 'advisors', 'llc', 'lp']):
        return 'fund'
    if any(t in name_lower for t in ['inc', 'corp', 'corporation', 'company']):
        return 'corporate'
    return 'unknown'


def generate_linkedin_search_url(name: str) -> str:
    encoded = name.replace(' ', '%20')
    return f'https://www.linkedin.com/search/results/companies/?keywords={encoded}'


def emit_debug_report(investors: list, label: str) -> None:
    """Log a compact investor list for Railway debugging."""
    logger.info(f'--- {label} ({len(investors)}) ---')
    by_company: dict = {}
    for inv in investors:
        by_company.setdefault(inv['company_ipo'], []).append(inv)
    for company, group in by_company.items():
        logger.info(f'  {company}: {len(group)} investors')
        for inv in group:
            pct = f' {inv["ownership_pct"]}%' if inv.get('ownership_pct') else ''
            cls = inv.get('investor_class', '')
            flag = ' [13F]' if inv.get('verified_13f') else ''
            logger.info(f'    - {inv["investor_name"]}{pct} [{cls}]{flag}')


def main():
    logger.info('=' * 60)
    logger.info('Starting S-1 Prospector Weekly Run')
    logger.info('=' * 60)

    days_back = int(os.getenv('DAYS_BACK', 7))

    # Step 1: Fetch S-1 filings from EDGAR
    logger.info(f'STEP 1: Fetching S-1 filings from the last {days_back} days...')
    all_filings = get_recent_s1_filings(days_back=days_back)
    logger.info(f'Found {len(all_filings)} raw S-1 filings')

    if not all_filings:
        logger.warning('No S-1 filings found. Exiting.')
        return []

    total_filings_scanned = len(all_filings)

    # Step 2: Filter out SPACs and biotech/life science companies
    logger.info('STEP 2: Filtering SPAC and biotech/life science filings...')
    filings = filter_filings(all_filings)
    total_filings_after_filter = len(filings)

    if not filings:
        logger.warning('No filings remain after sector filter.')
        return []

    for filing in filings:
        logger.info(f'  Kept: {filing["company_name"]} (Filed: {filing["filing_date"]})')

    # Step 3: Fetch company descriptions for each kept filing
    logger.info('STEP 3: Fetching company descriptions...')
    filing_descriptions: dict = {}
    for filing in filings:
        name = filing['company_name']
        desc = get_company_description(filing)
        filing_descriptions[name] = desc
        if desc:
            logger.info(f'  {name}: {desc[:80]}...' if len(desc) > 80 else f'  {name}: {desc}')
        else:
            logger.info(f'  {name}: (no description found)')

    # Step 4: Parse stockholders from each filing
    logger.info('STEP 4: Parsing stockholder tables...')
    all_investors = []

    for i, filing in enumerate(filings, 1):
        logger.info(f'[{i}/{len(filings)}] {filing["company_name"]}')
        stockholders = parse_stockholders(filing)

        if stockholders:
            logger.info(f'  Found {len(stockholders)} stockholders')
        else:
            logger.warning(f'  No stockholders extracted')

        for stockholder in stockholders:
            all_investors.append({
                'investor_name': stockholder['name'],
                'company_ipo': filing['company_name'],
                'filing_date': filing['filing_date'],
                'ownership_pct': stockholder.get('ownership_pct', ''),
                'shares': stockholder.get('shares', ''),
                'entity_type': classify_entity(stockholder['name']),
                'in_crm': False,
                'crm_status': '',
                'crm_last_activity': '',
                'crm_notes': '',
                'foundation_contacts': '',
                'linkedin_search_url': generate_linkedin_search_url(stockholder['name']),
                'investor_class': '',
                'lp_qualified': False,
                'verified_13f': False,
            })

    logger.info(f'Total raw investor records: {len(all_investors)}')

    if not all_investors:
        logger.warning('No investors extracted. Exiting.')
        return []

    # Step 5: Qualify investors — 13F check for institutional, pass-through for individuals
    logger.info('STEP 5: Qualifying investors (13F check + name signals)...')
    qualified_investors = qualify_investors(all_investors)
    emit_debug_report(qualified_investors, 'Qualified LP prospects')

    # Step 6: Save full CSV for records (all investors, not just qualified)
    timestamp = datetime.now().strftime('%Y-%m-%d')
    filename = f's1_investors_{timestamp}.csv'
    write_to_csv(all_investors, filename)
    logger.info(f'Full CSV saved: {filename}')

    # Step 7: Post to #fundraising-bot on Slack
    logger.info('STEP 7: Sending Slack notification...')
    sent = send_weekly_report(
        qualified_investors=qualified_investors,
        filing_descriptions=filing_descriptions,
        total_filings_scanned=total_filings_scanned,
        total_filings_after_filter=total_filings_after_filter,
        run_date=timestamp,
    )
    if sent:
        logger.info('Slack message sent successfully')
    else:
        logger.warning('Slack message not sent (check SLACK_BOT_TOKEN env var)')

    logger.info('=' * 60)
    logger.info('RUN COMPLETE')
    logger.info(f'Filings scanned: {total_filings_scanned}')
    logger.info(f'After sector filter: {total_filings_after_filter}')
    logger.info(f'Raw investors extracted: {len(all_investors)}')
    logger.info(f'Qualified LP prospects: {len(qualified_investors)}')
    logger.info('=' * 60)

    return qualified_investors


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info('Run interrupted by user')
    except Exception as e:
        logger.error(f'FATAL ERROR: {e}', exc_info=True)
