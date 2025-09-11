from flask import Flask, render_template, jsonify, request
import os

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/files/list')
def list_files():
    base_path = os.path.expanduser('~')  # Base user directory
    path = request.args.get('path', '/')
    requested_path = os.path.normpath(os.path.join(base_path, path.lstrip('/')))

    print(f"Requested path: {requested_path}")  # Debug

    # Security check: prevent access outside base_path
    if not requested_path.startswith(base_path):
        return jsonify({'error': 'Invalid path'}), 403

    if not os.path.exists(requested_path):
        return jsonify({'error': 'Path not found'}), 404

    if not os.access(requested_path, os.R_OK):
        return jsonify({'error': 'Permission denied'}), 403

    try:
        files = []
        for item in os.listdir(requested_path):
            if item.startswith('.'):
                continue  # Skip hidden files
            item_path = os.path.join(requested_path, item)
            if os.access(item_path, os.R_OK):
                files.append({
                    'name': item,
                    'path': os.path.join(path, item).replace('\\', '/'),
                    'type': 'directory' if os.path.isdir(item_path) else 'file'
                })

        return jsonify(sorted(files, key=lambda x: (x['type'] != 'directory', x['name'])))
    except Exception as e:
        print(f"Error listing files: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/files/view')
def view_file():
    base_path = os.path.expanduser('~')
    path = request.args.get('path', '')
    requested_path = os.path.normpath(os.path.join(base_path, path.lstrip('/')))

    # Security check
    if not requested_path.startswith(base_path):
        return 'Invalid file path', 403

    if not os.path.exists(requested_path) or not os.path.isfile(requested_path):
        return 'File not found', 404

    try:
        with open(requested_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        print(f"Error reading file: {e}")
        return str(e), 500

if __name__ == '__main__':
    app.run(debug=True)
