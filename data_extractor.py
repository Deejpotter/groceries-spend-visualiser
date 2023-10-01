import re
import pandas as pd


def extract_data(text):
    """
    Extracts necessary data from the cleaned text.

    Parameters:
        text (str): The cleaned text.

    Returns:
        pd.DataFrame: The structured data.
    """
    # Regular expressions to identify patterns
    item_pattern = re.compile(r'(\d+)\s+(.+?)\s+(\d+)\s+(\d+\.\d{2})\s+(\d+\.\d{2})')

    # Extracting data
    items = item_pattern.findall(text)

    # Structuring data
    data = {
        'Item Description': [],
        'Quantity Ordered': [],
        'Quantity Supplied': [],
        'Price': [],
        'Amount': []
    }

    for item in items:
        data['Item Description'].append(item[1])
        data['Quantity Ordered'].append(int(item[2]))
        data['Quantity Supplied'].append(int(item[3]))
        data['Price'].append(float(item[4]))
        data['Amount'].append(float(item[5]))

    df = pd.DataFrame(data)
    return df

# Usage:
# structured_data = extract_data(cleaned_text)
