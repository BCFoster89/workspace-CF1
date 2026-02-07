"""
Text-to-CAD Application Backend (Refined)
Converts natural language descriptions to CadQuery code and generates STEP files.
"""

import os
import uuid
import tempfile
import traceback
import requests
import re
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='static')
CORS(app)

# Directory for storing generated STEP files
STEP_DIR = os.path.join(tempfile.gettempdir(), 'text-to-cad-steps')
os.makedirs(STEP_DIR, exist_ok=True)

# Ollama configuration
OLLAMA_BASE_URL = os.environ.get('OLLAMA_URL', 'http://localhost:11434')
# 'deepseek-coder' or 'codellama' are recommended, but llama3.2 works with this prompt.
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'llama3.2') 

CADQUERY_SYSTEM_PROMPT = """You are a specialized Text-to-CAD translator. Your ONLY goal is to output valid CadQuery Python code.

### CONSTRAINTS:
1. Output ONLY Python code. No conversational text, no markdown backticks, no explanations.
2. The final 3D object MUST be assigned to the variable 'result'.
3. Use 'mm' as the internal logic (CadQuery is unitless, assume 1 unit = 1mm).
4. Always start with 'import cadquery as cq'.

### CADQUERY SYNTAX RULES:
- Create base: `result = cq.Workplane("XY").box(length, width, height)`
- Select faces for features: Use `.faces(">Z")` for top, `"<Z"` for bottom, `">Y"` for back.
- To draw on a face: You MUST call `.workplane()` after selecting a face.
- Example for hole: `.faces(">Z").workplane().hole(diameter)`
- Filleting: `.edges()`.fillet(radius)`

### EXAMPLE OUTPUT FOR "A 10mm cube with a 5mm hole":
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10).faces(">Z").workplane().hole(5)
"""

def clean_llm_code(raw_code: str) -> str:
    """Removes markdown and conversational fluff from LLM response."""
    # Remove markdown code blocks
    code = re.sub(r'```python\s*|```\s*', '', raw_code)
    
    # Filter for lines that actually look like Python/CadQuery
    lines = code.split('\n')
    cleaned_lines = []
    for line in lines:
        # Ignore common LLM conversational prefixes
        if line.strip().lower().startswith(("here is", "sure", "this code", "below is")):
            continue
        cleaned_lines.append(line)
        
    return '\n'.join(cleaned_lines).strip()

def is_safe_code(code: str) -> bool:
    """Basic security check to prevent execution of malicious commands."""
    forbidden = ["os.", "sys.", "subprocess", "eval(", "open(", "requests.", "socket", "__import__"]
    return not any(item in code for item in forbidden)

def execute_cadquery(code: str) -> tuple[str, str]:
    """Execute CadQuery code and save to STEP."""
    if not is_safe_code(code):
        return None, "Security Error: Forbidden modules or functions detected in generated code."

    try:
        import cadquery as cq
        # Create isolated namespace
        namespace = {'cq': cq}
        
        # Execute the string as Python code
        exec(code, namespace)

        if 'result' not in namespace:
            return None, "The model failed to define the 'result' variable."

        result = namespace['result']
        file_id = str(uuid.uuid4())
        step_path = os.path.join(STEP_DIR, f"{file_id}.step")

        # Export
        cq.exporters.export(result, step_path)
        return file_id, None

    except Exception:
        error_msg = traceback.format_exc()
        return None, f"Execution Error:\n{error_msg}"

@app.route('/api/generate', methods=['POST'])
def generate():
    try:
        data = request.get_json()
        description = data.get('description', '').strip()

        if not description:
            return jsonify({'success': False, 'error': 'No description provided'}), 400

        # Request from Ollama
        prompt = f"{CADQUERY_SYSTEM_PROMPT}\n\nGenerate code for: {description}"
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": 512}
            },
            timeout=120
        )
        response.raise_for_status()
        
        raw_response = response.json().get('response', '')
        cleaned_code = clean_llm_code(raw_response)

        # Execute
        file_id, error = execute_cadquery(cleaned_code)

        if error:
            return jsonify({'success': False, 'code': cleaned_code, 'error': error})

        return jsonify({'success': True, 'code': cleaned_code, 'file_id': file_id})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/step/<file_id>')
def get_step(file_id):
    # Basic sanitization
    if not re.match(r'^[a-f0-9\-]+$', file_id):
        return jsonify({'error': 'Invalid ID'}), 400

    step_path = os.path.join(STEP_DIR, f"{file_id}.step")
    if not os.path.exists(step_path):
        return jsonify({'error': 'File not found'}), 404

    return send_file(step_path, mimetype='application/step', as_attachment=True, download_name="model.step")

# --- Retaining your original chat and index routes for UI compatibility ---
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/chat', methods=['POST'])
def chat():
    # ... (Keep your existing chat logic here for general UI conversation)
    pass

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
