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

IMPORTANT RULES:
1. Always start with: import cadquery as cq
2. The final result MUST be assigned to a variable called 'result'
3. Only output Python code - NO explanations, NO markdown, NO comments
4. Use millimeters as the default unit unless user specifies inches (convert inches to mm: 1 inch = 25.4mm)

CRITICAL SYNTAX NOTES:
- Use .edges().fillet(radius) to fillet ALL edges
- Use .edges("|Z").fillet(radius) to fillet edges parallel to Z axis
- Use .edges(">Z").fillet(radius) to fillet edges on top face
- Use .faces(">Z") to select the top face, .faces("<Z") for bottom
- Use .faces(">X") for front face, .faces("<X") for back face
- The .fillet() and .chamfer() methods take a SINGLE radius/distance value
- For holes: .hole(diameter) NOT .hole(radius)

Common operations:
- Box: cq.Workplane("XY").box(length, width, height)
- Cylinder: cq.Workplane("XY").cylinder(height, radius)
- Hole in face: .faces(">Z").workplane().hole(diameter, depth)
- Through hole: .faces(">Z").workplane().hole(diameter)
- Fillet all edges: .edges().fillet(radius)
- Fillet specific edges: .edges("|Z").fillet(radius)
- Chamfer: .edges().chamfer(distance)
- Extrude: .circle(radius).extrude(height)

Example - cube with hole:
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10).faces(">Z").workplane().hole(5)

Example - cylinder:
import cadquery as cq
result = cq.Workplane("XY").cylinder(50, 20)

Example - rounded box with fillets on all edges:
import cadquery as cq
result = cq.Workplane("XY").box(30, 20, 10).edges().fillet(2)

Example - box with chamfered edges:
import cadquery as cq
result = cq.Workplane("XY").box(20, 20, 10).edges().chamfer(1)
"""

CADQUERY_MODIFY_PROMPT = """You are an expert CadQuery programmer. Modify the existing code based on the user's request.

RULES:
1. Keep: import cadquery as cq
2. Result MUST be assigned to 'result'
3. Keep ALL previous features unless user asks to remove them
4. Output ONLY Python code - NO explanations, NO markdown
5. Convert inches to mm if needed (1 inch = 25.4mm)

CRITICAL SYNTAX:
- Fillet all edges: .edges().fillet(radius)
- Fillet specific edges: .edges("|Z").fillet(radius) or .edges(">Z").fillet(radius)
- For holes: .hole(diameter) NOT .hole(radius) - use DIAMETER not radius
- Select faces: .faces(">Z") for top, .faces("<Z") for bottom, .faces(">X") for front

Current code:
```python
{current_code}
```

User wants: {description}

Output the complete modified Python code:"""


# Common CadQuery attribute/method fixes (case sensitivity issues)
CADQUERY_FIXES = {
    '.length': '.Length',
    '.center': '.Center',
    '.area': '.Area',
    '.volume': '.Volume',
    '.normal': '.Normal',
    '.xdir': '.xDir',
    '.ydir': '.yDir',
    '.zdir': '.zDir',
    '.xlen': '.xLen',
    '.ylen': '.yLen',
    '.zlen': '.zLen',
    'Workplane("xy")': 'Workplane("XY")',
    'Workplane("xz")': 'Workplane("XZ")',
    'Workplane("yz")': 'Workplane("YZ")',
    'workplane("XY")': 'Workplane("XY")',
    'workplane("XZ")': 'Workplane("XZ")',
    'workplane("YZ")': 'Workplane("YZ")',
}


def fix_cadquery_code(code: str) -> str:
    """Apply common fixes to CadQuery code to handle case sensitivity and common mistakes."""
    fixed_code = code

    # Apply known fixes
    for wrong, correct in CADQUERY_FIXES.items():
        fixed_code = fixed_code.replace(wrong, correct)

    # Fix common pattern issues
    import re

    # Fix .fillet() called on single edge without proper syntax
    # This is a simple heuristic - more complex fixes might need AST parsing

    return fixed_code


def text_to_cadquery(description: str, previous_code: str = None) -> str:
    """Convert natural language description to CadQuery code using Ollama."""

    if previous_code:
        # Modifying existing model
        prompt = CADQUERY_MODIFY_PROMPT.format(
            current_code=previous_code,
            description=description
        )
    else:
        # Creating new model
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

    # Apply common fixes for CadQuery code
    code = fix_cadquery_code(code.strip())

    return code


def execute_cadquery(code: str, retry_with_fixes: bool = True) -> tuple[str, str, str]:
    """
    Execute CadQuery code and save the result as a STEP file.
    Returns (file_id, error_message, final_code).
    """
    import cadquery as cq

    # Apply fixes before first attempt
    code = fix_cadquery_code(code)

    def try_execute(code_to_run):
        """Attempt to execute the code."""
        namespace = {'cq': cq}
        exec(code_to_run, namespace)

        if 'result' not in namespace:
            raise ValueError("Code must define a 'result' variable with the CadQuery object")

        return namespace['result']

    try:
        result = try_execute(code)

        # Generate unique filename
        file_id = str(uuid.uuid4())
        step_path = os.path.join(STEP_DIR, f"{file_id}.step")

        # Export to STEP
        cq.exporters.export(result, step_path)

        return file_id, None, code

    except Exception as e:
        error_msg = str(e)

        # Try to auto-fix common errors and retry
        if retry_with_fixes:
            fixed_code = code

            # Fix specific error patterns
            if "'Edge' object has no attribute 'length'" in error_msg:
                fixed_code = fixed_code.replace('.length', '.Length')
            if "'Edge' object has no attribute 'center'" in error_msg:
                fixed_code = fixed_code.replace('.center', '.Center')
            if "'Face' object has no attribute 'area'" in error_msg:
                fixed_code = fixed_code.replace('.area', '.Area')
            if "'Solid' object has no attribute 'volume'" in error_msg:
                fixed_code = fixed_code.replace('.volume', '.Volume')

            # If we made changes, retry
            if fixed_code != code:
                try:
                    result = try_execute(fixed_code)
                    file_id = str(uuid.uuid4())
                    step_path = os.path.join(STEP_DIR, f"{file_id}.step")
                    cq.exporters.export(result, step_path)
                    return file_id, None, fixed_code
                except Exception:
                    pass  # Fall through to return original error

        return None, f"Error executing CadQuery code: {error_msg}\n{traceback.format_exc()}", code


@app.route('/')
def index():
    """Serve the main page."""
    return send_from_directory('static', 'index.html')


@app.route('/api/generate', methods=['POST'])
def generate():
    """
    Generate CAD model from text description.
    Expects JSON: {"description": "text description of the model", "previous_code": "optional existing code"}
    Returns JSON: {"success": bool, "code": str, "file_id": str, "error": str}
    """
    try:
        data = request.get_json()
        description = data.get('description', '').strip()
        previous_code = data.get('previous_code', '').strip() or None

        if not description:
            return jsonify({
                'success': False,
                'error': 'No description provided'
            }), 400

        # Convert text to CadQuery code (with optional previous code for iterative building)
        code = text_to_cadquery(description, previous_code)

        # Execute the code and generate STEP file
        file_id, error, final_code = execute_cadquery(code)

        if error:
            return jsonify({
                'success': False,
                'code': final_code,
                'error': error
            })

        return jsonify({
            'success': True,
            'code': final_code,  # Return the potentially fixed code
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
        file_id, error, final_code = execute_cadquery(code)

        if error:
            return jsonify({
                'success': False,
                'error': error
            })

        return jsonify({
            'success': True,
            'file_id': file_id,
            'code': final_code  # Return potentially fixed code
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
