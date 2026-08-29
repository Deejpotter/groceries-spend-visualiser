from flask import Flask, render_template, request
from process_invoices import process_invoices
from woolworths_analysis import load_order_history, analyze_order_history

app = Flask(__name__)


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        directory = 'invoices'
        analysis_results = process_invoices(directory)
        return render_template('index.html', results=analysis_results)

    return render_template('index.html')


@app.route('/woolworths')
def woolworths():
    df = load_order_history('data/woolworths_order_history.csv')
    stats = analyze_order_history(df)
    return render_template(
        'woolworths.html',
        stats=stats,
        monthly_labels=list(stats['monthly_spend'].index),
        monthly_values=list(stats['monthly_spend'].values),
    )


if __name__ == '__main__':
    app.run(debug=True)
