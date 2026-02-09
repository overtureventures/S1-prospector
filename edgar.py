"""
SEC EDGAR API integration for fetching and parsing S-1 filings.
"""

import re
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger(__name__)

# SEC requires a User-Agent header with contact info
SEC_HEADERS = {
    'User-Agent': 'S1Prospector/1.0 (contact@yourfirm.com)',  # Update with your info
    'Accept-Encoding': 'gzip, deflate'
}

EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions"
EDGAR_FILINGS_URL = "https://www.sec.gov/cgi-bin/browse-edgar"


def get_recent_s1_filings(days_back: int = 7) -> List[Dict]:
    """
    Fetch S-1 and S-1/A filings from the last N days.
    
    Returns list of filing metadata dicts.
    """
    filings = []
    
    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_back)
    
    # Use EDGAR full-text search API
    search_url = "https://efts.sec.gov/LATEST/search-index"
    
    # Alternative: Use the EDGAR filing search
    params = {
        'action': 'getcompany',
        'type': 'S-1',
        'dateb': '',
        'owner': 'include',
        'count': 100,
        'output': 'atom'
    }
    
    try:
        # Use the RSS feed for recent filings
        rss_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=S-1&company=&dateb=&owner=include&count=100&output=atom"
        response = requests.get(rss_url, headers=SEC_HEADERS, timeout=30)
        response.raise_for_status()
        
        # Parse the Atom feed
        soup = BeautifulSoup(response.content, 'xml')
        entries = soup.find_all('entry')
        
        for entry in entries:
            # Extract filing info
            title = entry.find('title').text if entry.find('title') else ''
            updated = entry.find('updated').text if entry.find('updated') else ''
            link = entry.find('link')['href'] if entry.find('link') else ''
            
            # Parse the title to extract form type and company
            # Format: "S-1 - Company Name (0001234567) (Filer)"
            title_match = re.match(r'(S-1(?:/A)?) - (.+?) \((\d+)\)', title)
            
            if title_match:
                form_type = title_match.group(1)
                company_name = title_match.group(2).strip()
                cik = title_match.group(3)
                
                # Parse the date
                try:
                    filing_date = datetime.fromisoformat(updated.replace('Z', '+00:00'))
                except:
                    filing_date = datetime.now()
                
                # Check if within our date range
                if filing_date.replace(tzinfo=None) >= start_date:
                    filings.append({
                        'form_type': form_type,
                        'company_name': company_name,
                        'cik': cik,
                        'filing_date': filing_date.strftime('%Y-%m-%d'),
                        'filing_url': link
                    })
        
        logger.info(f"Found {len(filings)} S-1 filings in date range")
        
    except requests.RequestException as e:
        logger.error(f"Error fetching EDGAR filings: {e}")
    
    return filings


def get_filing_document_url(cik: str, accession_number: str) -> Optional[str]:
    """Get the URL to the main S-1 document."""
    # Format accession number for URL
    acc_formatted = accession_number.replace('-', '')
    
    # Get filing index
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_formatted}/index.json"
    
    try:
        response = requests.get(index_url, headers=SEC_HEADERS, timeout=30)
        response.raise_for_status()
        index_data = response.json()
        
        # Find the main S-1 document (usually .htm)
        for item in index_data.get('directory', {}).get('item', []):
            name = item.get('name', '')
            if name.endswith('.htm') and 's-1' in name.lower():
                return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_formatted}/{name}"
        
        # Fallback: just get the first .htm file
        for item in index_data.get('directory', {}).get('item', []):
            if item.get('name', '').endswith('.htm'):
                return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_formatted}/{item['name']}"
                
    except requests.RequestException as e:
        logger.error(f"Error getting filing document URL: {e}")
    
    return None


def parse_stockholders(filing: Dict) -> List[Dict]:
    """
    Parse the principal stockholders table from an S-1 filing.
    
    This is the trickiest part - S-1 formats vary significantly.
    We look for common section headers and table patterns.
    """
    stockholders = []
    
    filing_url = filing.get('filing_url', '')
    if not filing_url:
        return stockholders
    
    try:
        # First, get the filing index page to find the actual document
        response = requests.get(filing_url, headers=SEC_HEADERS, timeout=30)
        response.raise_for_status()
        
        # Parse the index page to find the S-1 document link
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Look for the main document link
        doc_link = None
        for link in soup.find_all('a'):
            href = link.get('href', '')
            text = link.get_text().lower()
            if 's-1' in text or href.endswith('.htm'):
                if '/Archives/edgar/data/' in href:
                    doc_link = 'https://www.sec.gov' + href if href.startswith('/') else href
                    break
        
        if not doc_link:
            # Try to construct from the index page URL
            table = soup.find('table', {'class': 'tableFile'})
            if table:
                for row in table.find_all('tr'):
                    cells = row.find_all('td')
                    if len(cells) >= 3:
                        doc_type = cells[3].get_text().strip() if len(cells) > 3 else ''
                        if 'S-1' in doc_type or cells[0].find('a'):
                            link = cells[2].find('a') if len(cells) > 2 else cells[0].find('a')
                            if link:
                                href = link.get('href', '')
                                doc_link = 'https://www.sec.gov' + href if href.startswith('/') else href
                                break
        
        if not doc_link:
            logger.warning(f"Could not find S-1 document link for {filing['company_name']}")
            return stockholders
        
        # Now fetch the actual S-1 document
        logger.info(f"Fetching S-1 document: {doc_link}")
        doc_response = requests.get(doc_link, headers=SEC_HEADERS, timeout=60)
        doc_response.raise_for_status()
        
        doc_soup = BeautifulSoup(doc_response.content, 'html.parser')
        
        # Find the stockholders section
        stockholders = extract_stockholder_table(doc_soup)
        
        logger.info(f"Extracted {len(stockholders)} stockholders from {filing['company_name']}")
        
    except requests.RequestException as e:
        logger.error(f"Error parsing stockholders from {filing['company_name']}: {e}")
    
    return stockholders


def is_valid_investor_name(name: str) -> bool:
    """
    Check if a string looks like a valid investor name vs a section header.
    Returns True if it looks like a real investor name.
    """
    name_lower = name.lower().strip()
    name_upper = name.upper().strip()
    
    # Skip if too short or too long
    if len(name) < 3 or len(name) > 150:
        return False
    
    # If entire name is uppercase and more than 3 words, likely a section header
    words = name.split()
    if name == name_upper and len(words) > 4:
        return False
    
    # Exact matches to reject
    exact_rejects = [
        'directors and executive officers',
        'executive officers and directors', 
        'principal shareholders',
        'common stock',
        'class a common stock',
        'class b common stock',
        'preferred stock',
    ]
    # Check with percentage stripped
    name_no_pct = re.sub(r'\s*\([\d\.]+%\)\s*', '', name_lower).strip()
    name_no_pct = re.sub(r'\s*\(more than \d+%\).*', '', name_no_pct).strip()
    name_no_pct = name_no_pct.rstrip(':')
    
    if name_no_pct in exact_rejects:
        return False
    
    # Section headers to filter out (case-insensitive)
    section_headers = [
        'use of proceeds', 'plan of distribution', 'risk factors',
        'legal matters', 'experts', 'underwriting', 'indemnification',
        'available information', 'financial statements', 'part i',
        'part ii', 'item ', 'where you can find', 'material u.s.',
        'certain relationships', 'related transactions', 'description of',
        'dilution', 'capitalization', 'executive compensation',
        'security ownership', 'principal stockholders', 'selling stockholders',
        'shares eligible', 'tax considerations', 'erisa considerations',
        'validity of', 'legal proceedings', 'market for', 'dividend policy',
        'selected financial', 'properties', 'directors and officers',
        'table of contents', 'prospectus summary', 'the offering',
        'corporate governance', 'related party', 'beneficial owner',
        '5% or greater', 'information not required', 'limitation on',
        'release of funds', 'nasdaq', 'nyse', 'trading symbol',
        'disclosure of', 'commission position', 'index to', 'changes in',
        'disagreements with', 'accountants on', 'accounting and',
        'federal tax', 'non-u.s. holders', 'securities act',
        'forward-looking', 'summary of', 'overview', 'background',
        'how to', 'what is', 'why we', 'our business', 'our company',
        'financial condition', 'results of operations', 'liquidity',
        'critical accounting', 'recent developments', 'industry',
        'competition', 'intellectual property', 'government regulation',
        'employees', 'facilities', 'legal proceedings',
        'principal shareholders', 'more than 5%', 'class a', 'class b',
        'common stock', 'preferred stock', 'series a', 'series b',
    ]
    
    for header in section_headers:
        if header in name_lower:
            return False
    
    # Skip if starts with common non-name patterns
    bad_starts = [
        'name', 'total', '(', '_', '*', '-', '—', 'note', 'see ',
        'the ', 'our ', 'we ', 'an ', 'a ', 'all executive', 'all directors',
        'officers and directors as a group', 'executive officers and directors',
        'item', 'part', 'section', 'article', 'exhibit', 'schedule',
        'index', 'table', 'summary', 'overview', 'introduction',
        'directors and', 'executive officers',
    ]
    for start in bad_starts:
        if name_lower.startswith(start):
            # Exception: "All executive officers..." with share data is valid
            if 'as a group' in name_lower and any(c.isdigit() for c in name):
                return True
            return False
    
    # Skip if it's just numbers or special characters
    if re.match(r'^[\d\s\.\,\%\$\(\)\-]+$', name):
        return False
    
    # Skip if it looks like a footnote
    if re.match(r'^\(\d+\)', name) or re.match(r'^\*+', name):
        return False
    
    # Skip if it ends with common section endings
    bad_endings = ['statements', 'information', 'considerations', 'matters', 
                   'disclosure', 'liability', 'liabilities']
    for ending in bad_endings:
        if name_lower.endswith(ending):
            return False
    
    # Valid investor names usually contain:
    # - Person names (capitalized words, 2-5 words)
    # - Entity names (LLC, LP, Inc, Corp, Fund, Capital, Partners, Trust, etc.)
    entity_indicators = [
        'llc', 'llp', 'l.l.c', 'l.p.', ' lp', 'inc', 'corp', 'corporation',
        'fund', 'capital', 'partners', 'venture', 'trust', 'holdings',
        'management', 'advisors', 'investment', 'equity', 'group',
        'foundation', 'endowment', 'family', 'associates', 'asset',
        'securities', 'limited', 'ltd', 'company', 'co.', ' gp',
        'partnership',
    ]
    
    # Check if it looks like an entity
    has_entity_indicator = any(ind in name_lower for ind in entity_indicators)
    
    # Check if it looks like a person name (2-5 capitalized words, not all caps)
    capitalized_words = sum(1 for w in words if w and w[0].isupper() and not w.isupper())
    looks_like_person = 2 <= len(words) <= 5 and capitalized_words >= 2 and name != name_upper
    
    # Check if it has credentials (Ph.D., M.D., M.B.A., etc.) - indicates a person
    has_credentials = bool(re.search(r'\b(Ph\.?D|M\.?D|M\.?B\.?A|J\.?D|CPA|CFA)\b', name, re.I))
    
    # If it has ownership percentage embedded, probably valid
    if re.search(r'\d+\.?\d*%', name):
        # But make sure it's not just a percentage phrase
        if has_entity_indicator or looks_like_person or has_credentials:
            return True
    
    # Must look like either an entity or a person
    if has_entity_indicator or looks_like_person or has_credentials:
        return True
    
    # Last resort: if short (2-3 words) and has a capitalized structure, might be a name
    if len(words) >= 2 and len(words) <= 4:
        # Check if it looks like "First Last" pattern
        if all(w[0].isupper() for w in words if w):
            return True
    
    return False


def extract_stockholder_table(soup: BeautifulSoup) -> List[Dict]:
    """
    Extract stockholder information from the parsed S-1 document.
    
    Looks for sections titled:
    - "Principal Stockholders"
    - "Principal and Selling Stockholders"
    - "Security Ownership of Certain Beneficial Owners"
    - "Beneficial Ownership"
    """
    stockholders = []
    
    # Common section header patterns
    header_patterns = [
        r'principal\s+(and\s+selling\s+)?stockholders',
        r'security\s+ownership',
        r'beneficial\s+owner',
        r'selling\s+stockholders',
        r'principal\s+shareholders'
    ]
    
    # Find tables that might contain stockholder data
    tables = soup.find_all('table')
    
    for table in tables:
        # Check if this table or nearby text contains stockholder-related headers
        table_text = table.get_text().lower()
        preceding_text = ''
        
        # Get preceding siblings/parents to check for section header
        for prev in table.find_all_previous(limit=5):
            if prev.name in ['h1', 'h2', 'h3', 'h4', 'p', 'div', 'b', 'strong']:
                preceding_text = prev.get_text().lower()
                break
        
        is_stockholder_table = False
        for pattern in header_patterns:
            if re.search(pattern, table_text) or re.search(pattern, preceding_text):
                is_stockholder_table = True
                break
        
        # Also check for column headers that indicate stockholder tables
        if 'beneficial' in table_text and ('shares' in table_text or 'percent' in table_text):
            is_stockholder_table = True
        
        if is_stockholder_table:
            rows = table.find_all('tr')
            
            # Try to identify header row and data rows
            header_row_idx = None
            for idx, row in enumerate(rows):
                row_text = row.get_text().lower()
                if ('name' in row_text and ('shares' in row_text or 'percent' in row_text)) or \
                   ('beneficial owner' in row_text):
                    header_row_idx = idx
                    break
            
            if header_row_idx is not None:
                # Parse data rows
                for row in rows[header_row_idx + 1:]:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 2:
                        name_cell = cells[0].get_text().strip()
                        
                        # Clean up the name
                        name = re.sub(r'\s+', ' ', name_cell)
                        name = re.sub(r'\(\d+\)', '', name)  # Remove footnote references like (1), (2)
                        name = re.sub(r'\([a-z]\)', '', name, flags=re.I)  # Remove (a), (b), etc.
                        name = name.strip()
                        
                        # Validate that this looks like a real investor name
                        if not is_valid_investor_name(name):
                            continue
                        
                        stockholder = {'name': name}
                        
                        # Try to extract shares and percentage from other cells
                        for cell in cells[1:]:
                            cell_text = cell.get_text().strip()
                            
                            # Look for percentage
                            pct_match = re.search(r'(\d+\.?\d*)%', cell_text)
                            if pct_match and 'ownership_pct' not in stockholder:
                                stockholder['ownership_pct'] = pct_match.group(1)
                            
                            # Look for share count
                            share_match = re.search(r'([\d,]+)', cell_text.replace('%', ''))
                            if share_match and 'shares' not in stockholder:
                                shares = share_match.group(1).replace(',', '')
                                if shares.isdigit() and int(shares) > 100:
                                    stockholder['shares'] = shares
                        
                        stockholders.append(stockholder)
            
            # Only process one stockholder table
            if stockholders:
                break
    
    return stockholders


def get_filing_details(cik: str) -> Dict:
    """Get detailed company filing information."""
    url = f"{EDGAR_SUBMISSIONS_URL}/CIK{cik.zfill(10)}.json"
    
    try:
        response = requests.get(url, headers=SEC_HEADERS, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Error fetching company details for CIK {cik}: {e}")
        return {}
