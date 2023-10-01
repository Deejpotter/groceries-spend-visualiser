from flask import Flask, render_template, request
from process_invoices import process_invoices

app = Flask(__name__)


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        directory = 'invoices'
        analysis_results = process_invoices(directory)
        return render_template('index.html', results=analysis_results)

    return render_template('index.html')


if __name__ == '__main__':
    app.run(debug=True)
