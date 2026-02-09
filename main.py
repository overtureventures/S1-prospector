"""
S-1 Prospector - Simplified Version
Scans SEC EDGAR for recent S-1 filings, extracts investor data,
enriches with foundation 990s, and outputs to console/CSV.
"""

import os
import logging
from datetime import datetime
from dotenv import load_dotenv

from edgar import get_recent_s1_filings, parse_stockholders
from propublica import lookup_foundation_officers
from output import write_to_csv

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


def print_results_to_console(investors, run_date):
    """
    Print investor results in a copyable format to Railway logs.
    """
    print("\n\n")
    print("=" * 100)
    print("📋 WEEKLY S-1 INVESTOR REPORT - " + run_date)
    print("=" * 100)
    print()
    
    if not investors:
        print("   No investors found this week.")
        print("=" * 100)
        return
    
    # Group by company
    by_company = {}
    for inv in investors:
        company = inv['company_ipo']
        if company not in by_company:
            by_company[company] = []
        by_company[company].append(inv)
    
    # Print by company
    for company, company_investors in by_company.items():
        print(f"\n{'─' * 100}")
        print(f"🏢  {company.upper()}")
        print(f"{'─' * 100}")
        print(f"Filing Date: {company_investors[0]['filing_date']}")
        print(f"Total Investors Found: {len(company_investors)}\n")
        
        for i, inv in enumerate(company_investors, 1):
            print(f"{i}. {inv['investor_name']}")
            print(f"   └─ Type: {inv['entity_type'].replace('_', ' ').title()}")
            
            details = []
            if inv['ownership_pct']:
                details.append(f"Ownership: {inv['ownership_pct']}%")
            if inv['shares']:
                details.append(f"Shares: {inv['shares']}")
            if details:
                print(f"   └─ {' | '.join(details)}")
            
            if inv['foundation_contacts']:
                print(f"   └─ Foundation Contacts: {inv['foundation_contacts']}")
            
            print(f"   └─ LinkedIn: {inv['linkedin_search_url']}")
            print()
    
    # Summary stats
    print("\n" + "=" * 100)
    print("📊 WEEKLY SUMMARY")
    print("=" * 100)
    print(f"\nTotal IPO Filings: {len(by_company)}")
    print(f"Total Investors Identified: {len(investors)}\n")
    
    print("Breakdown by Entity Type:")
    entity_counts = {}
    for inv in investors:
        entity_type = inv['entity_type']
        entity_counts[entity_type] = entity_counts.get(entity_type, 0) + 1
    
    for entity_type, count in sorted(entity_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"   • {entity_type.replace('_', ' ').title()}: {count}")
    
    print("\n" + "=" * 100)
    print("✅ COPY THE ABOVE RESULTS AND SHARE WITH YOUR TEAM")
    print("=" * 100)
    print("\n\n")


def main():
    logger.info("=" * 60)
    logger.info("Starting S-1 Prospector Weekly Run")
    logger.info("=" * 60)
    
    # Configuration
    days_back = int(os.getenv('DAYS_BACK', 7))
    enrich_foundations = os.getenv('ENRICH_FOUNDATIONS', 'true').lower() == 'true'
    
    # Step 1: Get recent S-1 filings
    logger.info(f"\n📋 STEP 1: Fetching S-1 filings from the last {days_back} days...")
    filings = get_recent_s1_filings(days_back=days_back)
    logger.info(f"✓ Found {len(filings)} S-1 filings")
    
    if not filings:
        logger.warning("⚠️  No S-1 filings found in the specified time period")
        logger.info("This might be normal during slow IPO periods")
        return []
    
    # Show which companies we found
    logger.info("\nCompanies found:")
    for filing in filings:
        logger.info(f"  • {filing['company_name']} (Filed: {filing['filing_date']})")
    
    # Step 2: Parse stockholders from each filing
    logger.info(f"\n📊 STEP 2: Parsing stockholder tables...")
    all_investors = []
    
    for i, filing in enumerate(filings, 1):
        logger.info(f"\n[{i}/{len(filings)}] Processing: {filing['company_name']}")
        stockholders = parse_stockholders(filing)
        
        if stockholders:
            logger.info(f"  ✓ Found {len(stockholders)} stockholders")
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
        else:
            logger.warning(f"  ⚠️  No stockholders extracted from {filing['company_name']}")
            logger.warning(f"     This filing may have an unusual table format")
    
    logger.info(f"\n✓ Total investor records extracted: {len(all_investors)}")
    
    if not all_investors:
        logger.warning("⚠️  No investors extracted from any filings")
        logger.warning("This could indicate parsing issues - check the filing formats manually")
        return []
    
    # Show entity breakdown
    entity_counts = {}
    for investor in all_investors:
        entity_type = investor['entity_type']
        entity_counts[entity_type] = entity_counts.get(entity_type, 0) + 1
    
    logger.info("\nEntity type breakdown:")
    for entity_type, count in sorted(entity_counts.items(), key=lambda x: x[1], reverse=True):
        logger.info(f"  • {entity_type}: {count}")
    
    # Step 3: Enrich foundations with 990 data (optional)
    if enrich_foundations:
        foundations = [i for i in all_investors if i['entity_type'] == 'foundation']
        if foundations:
            logger.info(f"\n🔍 STEP 3: Looking up foundation 990 data ({len(foundations)} foundations)...")
            
            for i, investor in enumerate(foundations, 1):
                logger.info(f"  [{i}/{len(foundations)}] Looking up: {investor['investor_name']}")
                officers = lookup_foundation_officers(investor['investor_name'])
                
                if officers:
                    contacts = '; '.join([f"{o['name']} ({o['title']})" for o in officers[:5]])
                    investor['foundation_contacts'] = contacts
                    logger.info(f"    ✓ Found contacts: {contacts[:100]}...")
                else:
                    logger.info(f"    - No 990 data found")
        else:
            logger.info(f"\n⏭️  STEP 3: Skipped (no foundations found)")
    else:
        logger.info(f"\n⏭️  STEP 3: Foundation enrichment disabled")
    
    # Step 4: Save CSV backup
    timestamp = datetime.now().strftime('%Y-%m-%d')
    logger.info(f"\n💾 STEP 4: Saving CSV backup...")
    filename = f"s1_investors_{timestamp}.csv"
    write_to_csv(all_investors, filename)
    logger.info(f"✓ Saved to {filename}")
    
    # Step 5: Print results to console for easy copying
    print_results_to_console(all_investors, timestamp)
    
    # Final Summary
    logger.info("\n" + "=" * 60)
    logger.info("📈 RUN COMPLETE")
    logger.info("=" * 60)
    logger.info(f"S-1 Filings Processed: {len(filings)}")
    logger.info(f"Total Investors Found: {len(all_investors)}")
    logger.info(f"Foundations: {sum(1 for i in all_investors if i['entity_type'] == 'foundation')}")
    logger.info(f"Family Offices: {sum(1 for i in all_investors if i['entity_type'] == 'family_office')}")
    logger.info(f"Funds: {sum(1 for i in all_investors if i['entity_type'] == 'fund')}")
    logger.info("=" * 60)
    
    return all_investors


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\n\n⚠️  Run interrupted by user")
    except Exception as e:
        logger.error(f"\n\n❌ FATAL ERROR: {e}", exc_info=True)
