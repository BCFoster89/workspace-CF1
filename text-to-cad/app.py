"""
Text-to-CAD Application Backend
Converts natural language descriptions to CadQuery code and generates STEP files.
Uses Ollama for local LLM inference.
"""

import os
import uuid
import tempfile
import traceback
import requests
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='static')
CORS(app)

# Directory for storing generated STEP files
STEP_DIR = os.path.join(tempfile.gettempdir(), 'text-to-cad-steps')
os.makedirs(STEP_DIR, exist_ok=True)

# Ollama configuration
OLLAMA_BASE_URL = os.environ.get('OLLAMA_URL', 'http://localhost:11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'llama3.2')  # Default model, can be changed


CADQUERY_SYSTEM_PROMPT = """You are an expert CadQuery programmer. Your task is to convert natural language descriptions of 3D objects into valid CadQuery Python code.

Rules:
1. Always import cadquery as cq at the start
2. The final result MUST be assigned to a variable called 'result'
3. Use proper CadQuery methods and syntax
4. The code should be complete and executable
5. Only output the Python code, no explanations or markdown
6. Use millimeters as the default unit unless specified otherwise
7. Common operations:
   - cq.Workplane("XY").box(length, width, height) - creates a box
   - .circle(radius).extrude(height) - creates a cylinder
   - .hole(diameter) - creates a through hole
   - .cboreHole(diameter, cboreDiameter, cboreDepth) - counterbored hole
   - .fillet(radius) - fillets edges
   - .chamfer(distance) - chamfers edges
   - .cut(other_shape) - boolean subtraction
   - .union(other_shape) - boolean union
   - .intersect(other_shape) - boolean intersection

Example for "a cube with 10mm sides and a 5mm hole in the center":
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10).faces(">Z").workplane().hole(5)

Example for "a cylinder with radius 20mm and height 50mm":
import cadquery as cq
result = cq.Workplane("XY").circle(20).extrude(50)

Example for "a rounded box 30x20x10mm with 2mm fillets":
import cadquery as cq
result = cq.Workplane("XY").box(30, 20, 10).edges().fillet(2)
"""


def text_to_cadquery(description: str) -> str:
    """Convert natural language description to CadQuery code using Ollama."""

    prompt = f"{CADQUERY_SYSTEM_PROMPT}\n\nGenerate CadQuery code for: {description}"

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,  # Low temperature for more deterministic code
                    "num_predict": 1024
                }
            },
            timeout=120  # 2 minute timeout for generation
        )
        response.raise_for_status()

        result = response.json()
        code = result.get('response', '')

    except requests.exceptions.ConnectionError:
        raise ValueError(f"Cannot connect to Ollama at {OLLAMA_BASE_URL}. Make sure Ollama is running (ollama serve)")
    except requests.exceptions.Timeout:
        raise ValueError("Ollama request timed out. The model may be too slow or not loaded.")
    except Exception as e:
        raise ValueError(f"Ollama error: {str(e)}")

    # Clean up the code if it has markdown code blocks
    if "```python" in code:
        code = code.split("```python")[1].split("```")[0]
    elif "```" in code:
        code = code.split("```")[1].split("```")[0]

    return code.strip()


def execute_cadquery(code: str) -> tuple[str, str]:
    """
    Execute CadQuery code and save the result as a STEP file.
    Returns (step_file_path, error_message).
    """
    try:
        import cadquery as cq

        # Create a safe namespace for execution
        namespace = {'cq': cq}

        # Execute the code
        exec(code, namespace)

        # Get the result
        if 'result' not in namespace:
            return None, "Code must define a 'result' variable with the CadQuery object"

        result = namespace['result']

        # Generate unique filename
        file_id = str(uuid.uuid4())
        step_path = os.path.join(STEP_DIR, f"{file_id}.step")

        # Export to STEP
        cq.exporters.export(result, step_path)

        return file_id, None

    except Exception as e:
        return None, f"Error executing CadQuery code: {str(e)}\n{traceback.format_exc()}"


@app.route('/')
def index():
    """Serve the main page."""
    return send_from_directory('static', 'index.html')


@app.route('/api/generate', methods=['POST'])
def generate():
    """
    Generate CAD model from text description.
    Expects JSON: {"description": "text description of the model"}
    Returns JSON: {"success": bool, "code": str, "file_id": str, "error": str}
    """
    try:
        data = request.get_json()
        description = data.get('description', '').strip()

        if not description:
            return jsonify({
                'success': False,
                'error': 'No description provided'
            }), 400

        # Convert text to CadQuery code
        code = text_to_cadquery(description)

        # Execute the code and generate STEP file
        file_id, error = execute_cadquery(code)

        if error:
            return jsonify({
                'success': False,
                'code': code,
                'error': error
            })

        return jsonify({
            'success': True,
            'code': code,
            'file_id': file_id
        })

    except ValueError as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500


@app.route('/api/execute', methods=['POST'])
def execute():
    """
    Execute CadQuery code directly (for code editing/refinement).
    Expects JSON: {"code": "cadquery code"}
    Returns JSON: {"success": bool, "file_id": str, "error": str}
    """
    try:
        data = request.get_json()
        code = data.get('code', '').strip()

        if not code:
            return jsonify({
                'success': False,
                'error': 'No code provided'
            }), 400

        # Execute the code and generate STEP file
        file_id, error = execute_cadquery(code)

        if error:
            return jsonify({
                'success': False,
                'error': error
            })

        return jsonify({
            'success': True,
            'file_id': file_id
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500


@app.route('/api/step/<file_id>')
def get_step(file_id):
    """Download or view a generated STEP file."""
    # Sanitize file_id to prevent directory traversal
    if not file_id.replace('-', '').isalnum():
        return jsonify({'error': 'Invalid file ID'}), 400

    step_path = os.path.join(STEP_DIR, f"{file_id}.step")

    if not os.path.exists(step_path):
        return jsonify({'error': 'File not found'}), 404

    return send_file(
        step_path,
        mimetype='application/step',
        as_attachment=request.args.get('download', 'false').lower() == 'true',
        download_name=f"model-{file_id[:8]}.step"
    )


@app.route('/api/chat', methods=['POST'])
def chat():
    """
    General chat endpoint for conversation with the AI.
    Can handle follow-up questions and refinements.
    """
    try:
        data = request.get_json()
        messages = data.get('messages', [])

        if not messages:
            return jsonify({
                'success': False,
                'error': 'No messages provided'
            }), 400

        # Build conversation prompt for Ollama
        system_prompt = """You are a helpful assistant for a Text-to-CAD application.
You help users create 3D models by understanding their descriptions and suggesting improvements.
When users describe a 3D object, help them refine the description to be more precise.
You can also explain CadQuery code and suggest modifications.
Keep responses concise and helpful."""

        # Format messages into a single prompt
        prompt = system_prompt + "\n\n"
        for msg in messages:
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            if role == 'user':
                prompt += f"User: {content}\n"
            else:
                prompt += f"Assistant: {content}\n"
        prompt += "Assistant: "

        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.7,
                    "num_predict": 2048
                }
            },
            timeout=120
        )
        response.raise_for_status()

        result = response.json()

        return jsonify({
            'success': True,
            'response': result.get('response', '')
        })

    except requests.exceptions.ConnectionError:
        return jsonify({
            'success': False,
            'error': f'Cannot connect to Ollama at {OLLAMA_BASE_URL}. Make sure Ollama is running.'
        }), 500
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Error: {str(e)}'
        }), 500


if __name__ == '__main__':
    print(f"Using Ollama at: {OLLAMA_BASE_URL}")
    print(f"Using model: {OLLAMA_MODEL}")
    print(f"STEP files will be saved to: {STEP_DIR}")
    print("\nMake sure Ollama is running: ollama serve")
    print(f"And the model is pulled: ollama pull {OLLAMA_MODEL}")
    print("\nStarting server at http://localhost:5000")
    app.run(debug=True, host='0.0.0.0', port=5000)
