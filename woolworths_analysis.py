import pandas as pd


def load_order_history(csv_path):
    """
    Loads the itemized Woolworths order-history export.

    Parameters:
        csv_path (str): Path to the CSV (date, basket_id, channel, product_name,
            quantity, unit_price, cup_price, stockcode).

    Returns:
        pd.DataFrame
    """
    df = pd.read_csv(csv_path, parse_dates=['date'])
    df['line_total'] = df['quantity'] * df['unit_price'].fillna(0)
    return df


def analyze_order_history(df):
    """
    Computes spend/frequency stats from the order-history DataFrame.

    Returns:
        dict: totals, monthly spend, shop frequency, top products by
            order-frequency and by spend.
    """
    orders = df.drop_duplicates('basket_id')
    total_spend = df['line_total'].sum()
    first_date = df['date'].min()
    last_date = df['date'].max()
    span_days = (last_date - first_date).days or 1

    monthly = (
        df.assign(month=df['date'].dt.to_period('M').astype(str))
        .groupby('month')['line_total'].sum()
        .round(2)
    )

    per_product = df.groupby('product_name').agg(
        orders=('basket_id', 'nunique'),
        total_qty=('quantity', 'sum'),
        total_spend=('line_total', 'sum'),
        first_bought=('date', 'min'),
        last_bought=('date', 'max'),
    ).round(2)

    top_by_frequency = per_product.sort_values('orders', ascending=False).head(20)
    top_by_spend = per_product.sort_values('total_spend', ascending=False).head(20)

    return {
        'total_spend': round(total_spend, 2),
        'total_shops': len(orders),
        'first_date': first_date,
        'last_date': last_date,
        'span_days': span_days,
        'avg_days_between_shops': round(span_days / max(len(orders) - 1, 1), 1),
        'avg_spend_per_month': round(total_spend / (span_days / 30.44), 2),
        'avg_spend_per_shop': round(total_spend / len(orders), 2),
        'monthly_spend': monthly,
        'top_by_frequency': top_by_frequency,
        'top_by_spend': top_by_spend,
    }
