"""
Affinity CRM integration for matching investors against existing contacts.
"""

import requests
from typing import Dict, List, Optional
from fuzzywuzzy import fuzz
import logging

logger = logging.getLogger(__name__)

AFFINITY_API_URL = "https://api.affinity.co"


class AffinityClient:
    """Client for interacting with Affinity CRM API."""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.auth = ('', api_key)  # Affinity uses empty username with API key as password
        self.session.headers.update({
            'Content-Type': 'application/json'
        })
        
        self.organizations = {}  # name -> org data
        self.persons = {}  # name -> person data
        self.opportunities = {}  # org/person id -> opportunity data
        self.fundraising_list_id = None
    
    def _get(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make a GET request to the Affinity API."""
        try:
            url = f"{AFFINITY_API_URL}{endpoint}"
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Affinity API error: {e}")
            return None
    
    def get_lists(self) -> List[Dict]:
        """Get all lists in the Affinity account."""
        result = self._get('/lists')
        return result if result else []
    
    def get_list_by_name(self, name: str) -> Optional[Dict]:
        """Find a list by name."""
        lists = self.get_lists()
        for lst in lists:
            if lst.get('name', '').lower() == name.lower():
                return lst
        return None
    
    def get_list_entries(self, list_id: int) -> List[Dict]:
        """Get all entries in a list."""
        entries = []
        page_token = None
        
        while True:
            params = {'page_size': 500}
            if page_token:
                params['page_token'] = page_token
            
            result = self._get(f'/lists/{list_id}/list-entries', params=params)
            if not result:
                break
            
            entries.extend(result.get('list_entries', []))
            
            page_token = result.get('next_page_token')
            if not page_token:
                break
        
        return entries
    
    def get_organization(self, org_id: int) -> Optional[Dict]:
        """Get organization details by ID."""
        return self._get(f'/organizations/{org_id}')
    
    def get_person(self, person_id: int) -> Optional[Dict]:
        """Get person details by ID."""
        return self._get(f'/persons/{person_id}')
    
    def get_field_values(self, list_id: int, entry_id: int) -> List[Dict]:
        """Get field values for a list entry."""
        result = self._get(f'/lists/{list_id}/list-entries/{entry_id}/field-values')
        return result.get('field_values', []) if result else []
    
    def get_interactions(self, entity_type: str, entity_id: int, limit: int = 5) -> List[Dict]:
        """Get recent interactions for an entity."""
        params = {
            f'{entity_type}_id': entity_id,
            'page_size': limit
        }
        result = self._get('/interactions', params=params)
        return result.get('interactions', []) if result else []
    
    def load_fundraising_list(self, list_name: str = 'Fundraising'):
        """
        Load the fundraising list and cache organizations/persons.
        """
        logger.info(f"Loading Affinity list: {list_name}")
        
        # Find the list
        lst = self.get_list_by_name(list_name)
        if not lst:
            logger.error(f"Could not find Affinity list named '{list_name}'")
            return
        
        self.fundraising_list_id = lst['id']
        logger.info(f"Found list '{list_name}' with ID {self.fundraising_list_id}")
        
        # Get all entries
        entries = self.get_list_entries(self.fundraising_list_id)
        logger.info(f"Loaded {len(entries)} entries from list")
        
        # Process each entry
        for entry in entries:
            entity = entry.get('entity', {})
            entity_type = entry.get('entity_type')
            entity_id = entry.get('entity_id')
            
            # Get field values for this entry (opportunity data)
            field_values = self.get_field_values(self.fundraising_list_id, entry['id'])
            
            opportunity_data = {
                'entry_id': entry['id'],
                'entity_type': entity_type,
                'entity_id': entity_id,
                'status': '',
                'last_activity': '',
                'notes': ''
            }
            
            # Extract relevant field values
            for fv in field_values:
                field_name = fv.get('field', {}).get('name', '').lower()
                value = fv.get('value')
                
                if 'status' in field_name or 'stage' in field_name:
                    opportunity_data['status'] = value
                elif 'note' in field_name:
                    opportunity_data['notes'] = value
            
            # Get last interaction
            if entity_type and entity_id:
                interactions = self.get_interactions(
                    'organization' if entity_type == 0 else 'person',
                    entity_id,
                    limit=1
                )
                if interactions:
                    opportunity_data['last_activity'] = interactions[0].get('date', '')
            
            # Cache by entity type
            if entity_type == 0:  # Organization
                org = self.get_organization(entity_id)
                if org:
                    name = org.get('name', '').strip().lower()
                    self.organizations[name] = {
                        **org,
                        'opportunity': opportunity_data
                    }
            elif entity_type == 1:  # Person
                person = self.get_person(entity_id)
                if person:
                    name = f"{person.get('first_name', '')} {person.get('last_name', '')}".strip().lower()
                    self.persons[name] = {
                        **person,
                        'opportunity': opportunity_data
                    }
        
        logger.info(f"Cached {len(self.organizations)} organizations and {len(self.persons)} persons")
    
    def find_match(self, investor_name: str, threshold: int = 80) -> Optional[Dict]:
        """
        Find a matching organization or person in the CRM.
        
        Uses fuzzy matching to handle slight name variations.
        Returns match data with opportunity details if found.
        """
        investor_lower = investor_name.strip().lower()
        
        best_match = None
        best_score = 0
        
        # Check organizations
        for org_name, org_data in self.organizations.items():
            score = fuzz.ratio(investor_lower, org_name)
            
            # Also try partial matching for longer names
            partial_score = fuzz.partial_ratio(investor_lower, org_name)
            score = max(score, partial_score)
            
            if score > best_score and score >= threshold:
                best_score = score
                best_match = {
                    'type': 'organization',
                    'name': org_data.get('name', ''),
                    'match_score': score,
                    'status': org_data.get('opportunity', {}).get('status', ''),
                    'last_activity': org_data.get('opportunity', {}).get('last_activity', ''),
                    'notes': org_data.get('opportunity', {}).get('notes', ''),
                    'domain': org_data.get('domain', ''),
                    'affinity_id': org_data.get('id')
                }
        
        # Check persons
        for person_name, person_data in self.persons.items():
            score = fuzz.ratio(investor_lower, person_name)
            partial_score = fuzz.partial_ratio(investor_lower, person_name)
            score = max(score, partial_score)
            
            if score > best_score and score >= threshold:
                best_score = score
                best_match = {
                    'type': 'person',
                    'name': f"{person_data.get('first_name', '')} {person_data.get('last_name', '')}",
                    'match_score': score,
                    'status': person_data.get('opportunity', {}).get('status', ''),
                    'last_activity': person_data.get('opportunity', {}).get('last_activity', ''),
                    'notes': person_data.get('opportunity', {}).get('notes', ''),
                    'email': person_data.get('primary_email', ''),
                    'affinity_id': person_data.get('id')
                }
        
        if best_match:
            logger.info(f"Matched '{investor_name}' to '{best_match['name']}' (score: {best_score})")
        
        return best_match
    
    def search_all_organizations(self, query: str) -> List[Dict]:
        """
        Search all organizations in Affinity (not just the fundraising list).
        Useful for checking if an investor exists anywhere in the CRM.
        """
        params = {'term': query, 'page_size': 10}
        result = self._get('/organizations', params=params)
        return result.get('organizations', []) if result else []
