"""
S-1 Prospector
Scans SEC EDGAR for recent S-1 filings, extracts investor data,
enriches with foundation 990s, matches against Affinity CRM,
and outputs to Google Sheets.
"""

import os
import logging
from datetime import datetime
from dotenv import load_dotenv

from edgar import get_recent_s1_filings, parse_stockholders
from propublica import lookup_foundation_officers
from affinity import AffinityClient
from output import write_to_google_sheet, write_to_csv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()


def classify_entity(name: str) -> str:
    """Simple heuristic to classify investor entity type."""
    name_lower = name.lower()
    
    if any(term in name_lower for term in ['foundation', 'endowment']):
        return 'foundation'
    elif any(term in name_lower for term in ['family office', 'family trust', 'family lp']):
        return 'family_office'
    elif any(term in name_lower for term in ['trust', 'estate']):
        return 'trust'
    elif any(term in name_lower for term in ['capital', 'partners', 'ventures', 'fund', 'management', 'advisors', 'llc', 'lp']):
        return 'fund'
    elif any(term in name_lower for term in ['inc', 'corp', 'corporation', 'company']):
        return 'corporate'
    else:
        return 'unknown'


def generate_linkedin_search_url(name: str) -> str:
    """Generate a LinkedIn search URL for the entity."""
    encoded_name = name.replace(' ', '%20')
    return f"https://www.linkedin.com/search/results/companies/?keywords={encoded_name}"


def main():
    logger.info("Starting S-1 Prospector run")
    
    # Configuration
    days_back = int(os.getenv('DAYS_BACK', 7))
    output_method = os.getenv('OUTPUT_METHOD', 'csv')
    
    # Initialize Affinity client
    affinity_api_key = os.getenv('AFFINITY_API_KEY')
    logger.info(f"AFFINITY_API_KEY present: {bool(affinity_api_key)}")
    logger.info(f"AFFINITY_API_KEY length: {len(affinity_api_key) if affinity_api_key else 0}")
    
    if not affinity_api_key:
        logger.warning("No Affinity API key found - CRM matching will be skipped")
        affinity = None
    else:
        logger.info("Initializing Affinity client...")
        affinity = AffinityClient(affinity_api_key)
        list_name = os.getenv('AFFINITY_LIST_NAME', 'Fundraising')
        logger.info(f"Loading Affinity list: {list_name}")
        affinity.load_fundraising_list(list_name)
    
    # Step 1: Get recent S-1 filings
    logger.info(f"Fetching S-1 filings from the last {days_back} days")
    filings = get_recent_s1_filings(days_back=days_back)
    logger.info(f"Found {len(filings)} S-1 filings")
    
    # Step 2: Parse stockholders from each filing
    all_investors = []
    for filing in filings:
        logger.info(f"Parsing stockholders from {filing['company_name']} ({filing['cik']})")
        stockholders = parse_stockholders(filing)
        
        for stockholder in stockholders:
            investor = {
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
                'linkedin_search_url': generate_linkedin_search_url(stockholder['name'])
            }
            all_investors.append(investor)
    
    logger.info(f"Extracted {len(all_investors)} total investor records")
    
    # Step 3: Enrich foundations with 990 data
    logger.info("Looking up foundation 990s")
    for investor in all_investors:
        if investor['entity_type'] == 'foundation':
            officers = lookup_foundation_officers(investor['investor_name'])
            if officers:
                investor['foundation_contacts'] = '; '.join(
                    [f"{o['name']} ({o['title']})" for o in officers[:5]]
                )
    
    # Step 4: Match against Affinity CRM
    if affinity:
        logger.info("Matching against Affinity CRM")
        for investor in all_investors:
            match = affinity.find_match(investor['investor_name'])
            if match:
                investor['in_crm'] = True
                investor['crm_status'] = match.get('status', '')
                investor['crm_last_activity'] = match.get('last_activity', '')
                investor['crm_notes'] = match.get('notes', '')
    
    # Step 5: Output results
    timestamp = datetime.now().strftime('%Y-%m-%d')
    
    if output_method == 'sheets':
        sheet_id = os.getenv('GOOGLE_SHEET_ID')
        if sheet_id:
            logger.info("Writing results to Google Sheets")
            write_to_google_sheet(all_investors, sheet_id, timestamp)
        else:
            logger.warning("No Google Sheet ID configured, falling back to CSV")
            write_to_csv(all_investors, f"s1_investors_{timestamp}.csv")
    else:
        logger.info("Writing results to CSV")
        write_to_csv(all_investors, f"s1_investors_{timestamp}.csv")
    
    logger.info(f"Run complete. Processed {len(all_investors)} investors from {len(filings)} filings.")
    
    # Summary stats
    in_crm = sum(1 for i in all_investors if i['in_crm'])
    foundations = sum(1 for i in all_investors if i['entity_type'] == 'foundation')
    logger.info(f"Summary: {in_crm} already in CRM, {foundations} foundations found")
    
    return all_investors


if __name__ == '__main__':
    main()
