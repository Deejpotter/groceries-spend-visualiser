from flask import Flask, render_template, request
app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # handle file upload and data processing here
        pass
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)
