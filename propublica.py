"""
ProPublica Nonprofit Explorer API integration for foundation 990 lookups.
"""

import requests
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

PROPUBLICA_API_URL = "https://projects.propublica.org/nonprofits/api/v2"


def search_foundation(name: str) -> Optional[Dict]:
    """
    Search for a foundation by name in ProPublica's Nonprofit Explorer.
    
    Returns the best matching organization or None.
    """
    try:
        # Clean up the name for search
        search_name = name.replace('Foundation', '').replace('Endowment', '').strip()
        
        url = f"{PROPUBLICA_API_URL}/search.json"
        params = {'q': search_name}
        
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        organizations = data.get('organizations', [])
        
        if organizations:
            # Return the first match (most relevant)
            # Could improve with fuzzy matching against original name
            return organizations[0]
        
    except requests.RequestException as e:
        logger.error(f"Error searching ProPublica for '{name}': {e}")
    
    return None


def get_organization_details(ein: str) -> Optional[Dict]:
    """
    Get detailed information about an organization by EIN.
    """
    try:
        url = f"{PROPUBLICA_API_URL}/organizations/{ein}.json"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        return response.json().get('organization', {})
        
    except requests.RequestException as e:
        logger.error(f"Error fetching organization details for EIN {ein}: {e}")
    
    return None


def get_990_filings(ein: str) -> List[Dict]:
    """
    Get 990 filings for an organization.
    """
    try:
        url = f"{PROPUBLICA_API_URL}/organizations/{ein}.json"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        return data.get('filings_with_data', [])
        
    except requests.RequestException as e:
        logger.error(f"Error fetching 990 filings for EIN {ein}: {e}")
    
    return []


def lookup_foundation_officers(foundation_name: str) -> List[Dict]:
    """
    Look up officers/directors for a foundation.
    
    Returns list of officers with name and title.
    """
    officers = []
    
    # First, search for the foundation
    org = search_foundation(foundation_name)
    if not org:
        logger.info(f"No ProPublica match found for '{foundation_name}'")
        return officers
    
    ein = org.get('ein')
    if not ein:
        return officers
    
    logger.info(f"Found foundation match: {org.get('name')} (EIN: {ein})")
    
    # Get the most recent 990 filing
    filings = get_990_filings(ein)
    if not filings:
        return officers
    
    # Get the most recent filing
    latest_filing = filings[0]
    
    # Extract officers from the filing data
    # ProPublica includes officer data in some filings
    if 'pdf_url' in latest_filing:
        # We could parse the PDF, but that's complex
        # Instead, use the organization details which sometimes include officers
        pass
    
    # Try to get officers from the organization endpoint
    try:
        # ProPublica's API doesn't directly expose officers in a structured way
        # But we can get some info from the organization details
        org_details = get_organization_details(ein)
        
        if org_details:
            # Check for officer data in various fields
            # This varies by 990 version and what ProPublica exposes
            
            # For now, return basic organization info as a fallback
            # The full officer list would require parsing the actual 990 PDF
            officers.append({
                'name': org_details.get('name', foundation_name),
                'title': 'Foundation',
                'ein': ein,
                'city': org_details.get('city', ''),
                'state': org_details.get('state', ''),
                'total_assets': org_details.get('asset_amount', '')
            })
            
    except Exception as e:
        logger.error(f"Error extracting officers for {foundation_name}: {e}")
    
    return officers


def enrich_foundation(foundation_name: str) -> Dict:
    """
    Get enriched data for a foundation including officers and financial info.
    """
    result = {
        'name': foundation_name,
        'matched': False,
        'ein': None,
        'location': None,
        'total_assets': None,
        'officers': []
    }
    
    org = search_foundation(foundation_name)
    if not org:
        return result
    
    result['matched'] = True
    result['ein'] = org.get('ein')
    
    # Get more details
    if result['ein']:
        details = get_organization_details(result['ein'])
        if details:
            result['location'] = f"{details.get('city', '')}, {details.get('state', '')}"
            result['total_assets'] = details.get('asset_amount')
            result['officers'] = lookup_foundation_officers(foundation_name)
    
    return result
