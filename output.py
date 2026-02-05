"""
Output module for writing results to Google Sheets or CSV.
"""

import os
import csv
from typing import List, Dict
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Google Sheets imports (optional)
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False
    logger.warning("gspread not available - Google Sheets output disabled")


def write_to_csv(investors: List[Dict], filename: str):
    """
    Write investor data to a CSV file.
    """
    if not investors:
        logger.warning("No investors to write")
        return
    
    # Define column order
    columns = [
        'investor_name',
        'company_ipo',
        'filing_date',
        'ownership_pct',
        'shares',
        'entity_type',
        'in_crm',
        'crm_status',
        'crm_last_activity',
        'crm_notes',
        'foundation_contacts'
