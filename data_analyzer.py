import pandas as pd


def analyze_data(df):
    """
    Analyzes the structured data.

    Parameters:
        df (pd.DataFrame): The structured data.

    Returns:
        dict: The analysis results.
    """
    total_spend = df['Amount'].sum()
    spend_by_item = df.groupby('Item Description')['Amount'].sum().sort_values(ascending=False)

    analysis_results = {
        'Total Spend': total_spend,
        'Spend by Item': spend_by_item
    }

    return analysis_results

# Usage:
# analysis_results = analyze_data(structured_data)
