"""Product identity and deployment-name compatibility, independent of stored data IDs."""
import os

NAME = 'AisleMint'
POS_NAME = 'AisleMint OneSales'
DEMO_RETAILER_NAME = 'AisleMint Demo · Simulated Retail'


def setting(suffix, default=''):
    """An explicitly configured AisleMint value wins, including an empty value."""
    return os.environ.get('AISLEMINT_' + suffix, os.environ.get('HINDSIGHT_' + suffix, default))


def retailer_name(row):
    # Present the reserved legacy demo under its current brand without changing
    # business records, stable namespace IDs, or a customer's own retailer name.
    if (row.get('id') == 'dbec888923f752659a34caf284055d60'
            and row.get('demo_package') == 'connected-retail-demo-v1'
            and row.get('name') == 'Hindsight Demo · Simulated Retail'):
        return DEMO_RETAILER_NAME
    return row['name']
