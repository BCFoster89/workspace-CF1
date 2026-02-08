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


CADQUERY_SYSTEM_PROMPT = """You are an expert CadQuery programmer. Convert natural language to CadQuery Python code.

CRITICAL RULES - FOLLOW EXACTLY:
1. Start with: import cadquery as cq
2. Assign final result to: result = ...
3. Output ONLY Python code - NO markdown, NO comments, NO explanations
4. Use millimeters (convert inches: 1 inch = 25.4mm)
5. Method arguments are SEPARATE values, NOT tuples: .box(10, 20, 30) NOT .box((10, 20, 30))
6. Every method call needs a dot: .box().faces() NOT .box()faces()

COPY THESE EXACT PATTERNS:

Example 1 - Simple box:
import cadquery as cq
result = cq.Workplane("XY").box(30, 20, 10)

Example 2 - Box with center hole:
import cadquery as cq
result = cq.Workplane("XY").box(80, 60, 10).faces(">Z").workplane().hole(22)

Example 3 - Box with filleted vertical edges:
import cadquery as cq
result = cq.Workplane("XY").box(30, 30, 5).edges("|Z").fillet(2)

Example 4 - Cylinder:
import cadquery as cq
result = cq.Workplane("XY").cylinder(50, 20)

Example 5 - Hollow box (shell):
import cadquery as cq
result = cq.Workplane("XY").box(20, 20, 20).shell(-2)

Example 6 - Box with 4 corner holes (counterbored):
import cadquery as cq
result = (
    cq.Workplane("XY")
    .box(40, 20, 5)
    .faces(">Z")
    .workplane()
    .rect(35, 15, forConstruction=True)
    .vertices()
    .cboreHole(2.4, 4.4, 2.1)
)

Example 7 - Plate with hole on side face:
import cadquery as cq
result = cq.Workplane("XY").box(20, 20, 30).faces(">X").workplane().hole(8, 10)

Example 8 - Extruded circle on top of box:
import cadquery as cq
result = cq.Workplane("XY").box(20, 20, 5).faces(">Z").workplane().circle(5).extrude(10)

Example 9 - Box with chamfered edges:
import cadquery as cq
result = cq.Workplane("XY").box(20, 20, 10).edges(">Z").chamfer(1)

Example 10 - Complete bearing block:
import cadquery as cq
result = (
    cq.Workplane("XY")
    .box(30, 40, 10)
    .faces(">Z")
    .workplane()
    .hole(22)
    .faces(">Z")
    .workplane()
    .rect(22, 32, forConstruction=True)
    .vertices()
    .cboreHole(2.4, 4.4, 2.1)
    .edges("|Z")
    .fillet(2)
)

FACE SELECTORS: ">Z"=top, "<Z"=bottom, ">X"=front, "<X"=back, ">Y"=right, "<Y"=left
EDGE SELECTORS: "|Z"=vertical, "|X"=parallel to X, ">Z"=top edges

NEVER USE: addWorkplane, createBox, makeHole, addFillet (these don't exist)
ALWAYS USE: .workplane(), .box(), .hole(), .fillet()"""

CADQUERY_MODIFY_PROMPT = """Modify the CadQuery code based on the user's request.

RULES:
1. Keep: import cadquery as cq
2. Assign to: result = ...
3. Keep ALL previous features unless asked to remove
4. Output ONLY Python code - NO markdown, NO comments
5. Arguments are SEPARATE: .box(10, 20, 30) NOT .box((10, 20, 30))
6. Every method needs a dot: .method1().method2()

VALID methods: .box(), .cylinder(), .sphere(), .hole(), .cboreHole(), .fillet(), .chamfer(), .shell(), .faces(), .edges(), .vertices(), .workplane(), .circle(), .rect(), .extrude(), .cut(), .union()

Face selectors: ">Z" (top), "<Z" (bottom), ">X", "<X", ">Y", "<Y"
Edge selectors: "|Z" (vertical), ">Z" (top edges), "<Z" (bottom edges)

Current code:
```python
{current_code}
```

User wants: {description}

Output the complete modified Python code:"""


# Common CadQuery attribute/method fixes (case sensitivity and hallucinated methods)
CADQUERY_FIXES = {
    # Case sensitivity fixes
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

    # Workplane case fixes
    'Workplane("xy")': 'Workplane("XY")',
    'Workplane("xz")': 'Workplane("XZ")',
    'Workplane("yz")': 'Workplane("YZ")',
    'workplane("XY")': 'Workplane("XY")',
    'workplane("XZ")': 'Workplane("XZ")',
    'workplane("YZ")': 'Workplane("YZ")',

    # Hallucinated method fixes - common LLM mistakes
    '.addWorkplane(': '.workplane(',
    '.AddWorkplane(': '.workplane(',
    '.add_workplane(': '.workplane(',
    '.newWorkplane(': '.workplane(',
    '.createWorkplane(': '.workplane(',

    '.createBox(': '.box(',
    '.makeBox(': '.box(',
    '.addBox(': '.box(',

    '.createCylinder(': '.cylinder(',
    '.makeCylinder(': '.cylinder(',
    '.addCylinder(': '.cylinder(',

    '.createHole(': '.hole(',
    '.makeHole(': '.hole(',
    '.addHole(': '.hole(',
    '.drillHole(': '.hole(',

    '.createFillet(': '.fillet(',
    '.makeFillet(': '.fillet(',
    '.addFillet(': '.fillet(',
    '.roundEdges(': '.fillet(',
    '.roundEdge(': '.fillet(',

    '.createChamfer(': '.chamfer(',
    '.makeChamfer(': '.chamfer(',
    '.addChamfer(': '.chamfer(',
    '.bevelEdges(': '.chamfer(',

    '.selectFace(': '.faces(',
    '.selectFaces(': '.faces(',
    '.getFace(': '.faces(',
    '.getFaces(': '.faces(',

    '.selectEdge(': '.edges(',
    '.selectEdges(': '.edges(',
    '.getEdge(': '.edges(',
    '.getEdges(': '.edges(',

    '.createShell(': '.shell(',
    '.makeShell(': '.shell(',
    '.hollowOut(': '.shell(',

    '.subtractFrom(': '.cut(',
    '.subtract(': '.cut(',
    '.boolean_cut(': '.cut(',
    '.booleanCut(': '.cut(',

    '.addTo(': '.union(',
    '.add(': '.union(',
    '.boolean_union(': '.union(',
    '.booleanUnion(': '.union(',
    '.combine(': '.union(',
    '.merge(': '.union(',

    # Face selector fixes
    'faces("top")': 'faces(">Z")',
    'faces("bottom")': 'faces("<Z")',
    'faces("front")': 'faces(">X")',
    'faces("back")': 'faces("<X")',
    'faces("right")': 'faces(">Y")',
    'faces("left")': 'faces("<Y")',
    'faces("TOP")': 'faces(">Z")',
    'faces("BOTTOM")': 'faces("<Z")',
    'faces("FRONT")': 'faces(">X")',
    'faces("BACK")': 'faces("<X")',

    # Common typos
    '.fillett(': '.fillet(',
    '.filet(': '.fillet(',
    '.chamf(': '.chamfer(',
    '.extrud(': '.extrude(',
}


# Valid CadQuery methods whitelist for validation
VALID_CADQUERY_METHODS = {
    # Workplane creation and navigation
    'Workplane', 'workplane', 'center', 'moveTo', 'lineTo', 'move', 'line',
    'vLine', 'hLine', 'vLineTo', 'hLineTo', 'polarLine', 'polarLineTo',
    'radiusArc', 'tangentArcPoint', 'threePointArc', 'sagittaArc', 'spline',
    'close', 'offset2D', 'mirrorY', 'mirrorX',

    # 3D Primitives
    'box', 'cylinder', 'sphere', 'cone', 'torus', 'wedge',

    # 2D Sketches
    'circle', 'ellipse', 'ellipseArc', 'rect', 'polygon', 'polyline',
    'slot2D', 'text', 'regularPolygon',

    # 3D Operations
    'extrude', 'revolve', 'sweep', 'loft', 'twistExtrude',

    # Hole operations
    'hole', 'cboreHole', 'cskHole', 'tapHole', 'threadedHole', 'pushPoints',

    # Modifiers
    'fillet', 'chamfer', 'shell', 'split', 'combine',

    # Boolean operations
    'cut', 'union', 'intersect', 'cutBlind', 'cutThruAll',

    # Selectors
    'faces', 'edges', 'vertices', 'wires', 'solids', 'shells', 'compounds',

    # Transformations
    'translate', 'rotate', 'rotateAboutCenter', 'mirror', 'scale',
    'transformed', 'offset',

    # Other common methods
    'val', 'vals', 'first', 'last', 'item', 'size', 'all', 'add', 'each',
    'eachpoint', 'end', 'clean', 'tag', 'copyWorkplane', 'newObject',
    'findSolid', 'findFace', 'section', 'toPending', 'consolidateWires',
}


def validate_structure(code: str) -> tuple[bool, str, str]:
    """
    Layer 1: Validate basic code structure.
    Returns (is_valid, error_message, fixed_code).
    """
    fixed_code = code

    # Check for import statement
    if 'import cadquery' not in fixed_code and 'from cadquery' not in fixed_code:
        # Add import at the beginning
        fixed_code = 'import cadquery as cq\n' + fixed_code

    # Check for result assignment
    if 'result' not in fixed_code or 'result=' not in fixed_code.replace(' ', '').replace('\n', ''):
        # Try to find the last workplane chain and assign it to result
        lines = fixed_code.strip().split('\n')
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i].strip()
            if line and not line.startswith('#') and not line.startswith('import'):
                if 'cq.Workplane' in line or '.box(' in line or '.cylinder(' in line:
                    if not line.startswith('result'):
                        lines[i] = 'result = ' + line
                    break
        fixed_code = '\n'.join(lines)

    # Check balanced parentheses
    paren_count = 0
    bracket_count = 0
    brace_count = 0
    in_string = False
    string_char = None

    for i, char in enumerate(fixed_code):
        # Handle string detection
        if char in '"\'':
            if not in_string:
                in_string = True
                string_char = char
            elif char == string_char and (i == 0 or fixed_code[i-1] != '\\'):
                in_string = False
                string_char = None
            continue

        if in_string:
            continue

        if char == '(':
            paren_count += 1
        elif char == ')':
            paren_count -= 1
        elif char == '[':
            bracket_count += 1
        elif char == ']':
            bracket_count -= 1
        elif char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1

    # Try to fix unbalanced parentheses
    if paren_count > 0:
        # Missing closing parens - add them at end
        fixed_code = fixed_code.rstrip() + ')' * paren_count
    elif paren_count < 0:
        # Extra closing parens - remove from end
        lines = fixed_code.rstrip().split('\n')
        last_line = lines[-1]
        while paren_count < 0 and last_line.endswith(')'):
            last_line = last_line[:-1]
            paren_count += 1
        lines[-1] = last_line
        fixed_code = '\n'.join(lines)

    return True, None, fixed_code


def fix_syntax_errors(code: str) -> str:
    """
    Layer 2: Fix common syntax errors including missing dots between method calls.
    """
    import re

    fixed_code = code

    # Fix tuple arguments: .method((a, b, c)) -> .method(a, b, c)
    # This handles LLM mistakes like .box((10, 20, 30)) instead of .box(10, 20, 30)
    # Pattern: .methodname(( followed by comma-separated values and ))
    tuple_methods = ['box', 'cylinder', 'sphere', 'hole', 'circle', 'rect', 'center',
                     'translate', 'moveTo', 'lineTo', 'cboreHole', 'cskHole', 'fillet',
                     'chamfer', 'shell', 'extrude', 'polygon']
    for method in tuple_methods:
        # Match .method((val1, val2, ...)) and convert to .method(val1, val2, ...)
        pattern = rf'\.{method}\(\(([^()]+)\)\)'
        fixed_code = re.sub(pattern, rf'.{method}(\1)', fixed_code)

    # Fix missing dots: )method( -> ).method(
    # This handles cases like: .box(10, 10, 10)faces(">Z") -> .box(10, 10, 10).faces(">Z")
    fixed_code = re.sub(r'\)([a-zA-Z_][a-zA-Z0-9_]*)\(', r').\1(', fixed_code)

    # Fix extra consecutive parentheses: )) where only one is needed
    # But be careful not to break valid code like nested function calls
    # Only fix cases like: .method())  ->  .method()
    fixed_code = re.sub(r'\(\)\)', '()', fixed_code)

    # Fix double dots: .. -> .
    fixed_code = re.sub(r'\.\.+', '.', fixed_code)

    # Fix spaces before dots in method chains
    fixed_code = re.sub(r'\s+\.(\w+)\(', r'.\1(', fixed_code)

    # Fix .workplane().workplane() duplication
    fixed_code = re.sub(r'\.workplane\(\)\.workplane\(\)', '.workplane()', fixed_code)

    # Fix direct method calls on Workplane without dot after constructor
    # cq.Workplane("XY")box(... -> cq.Workplane("XY").box(...
    fixed_code = re.sub(r'(cq\.Workplane\([^)]+\))([a-zA-Z_])', r'\1.\2', fixed_code)

    return fixed_code


def validate_methods(code: str) -> tuple[bool, list[str], str]:
    """
    Layer 3: Validate that all method calls are valid CadQuery methods.
    Returns (is_valid, list_of_invalid_methods, fixed_code).
    """
    import re

    fixed_code = code
    invalid_methods = []

    # Find all method calls (pattern: .methodName( )
    method_pattern = r'\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\('
    methods_found = re.findall(method_pattern, fixed_code)

    for method in methods_found:
        if method not in VALID_CADQUERY_METHODS:
            invalid_methods.append(method)

            # Try to find a fix in CADQUERY_FIXES
            for wrong, correct in CADQUERY_FIXES.items():
                if f'.{method}(' in wrong:
                    # Extract the correct method name from the fix
                    correct_method_match = re.search(r'\.(\w+)\(', correct)
                    if correct_method_match:
                        correct_method = correct_method_match.group(1)
                        fixed_code = re.sub(
                            rf'\.{re.escape(method)}\s*\(',
                            f'.{correct_method}(',
                            fixed_code
                        )
                        break

    return len(invalid_methods) == 0, invalid_methods, fixed_code


def fix_cadquery_code(code: str) -> str:
    """Apply comprehensive fixes to CadQuery code."""
    import re

    fixed_code = code

    # Layer 1: Structure validation
    _, _, fixed_code = validate_structure(fixed_code)

    # Layer 2: Apply known string replacements (hallucinated methods, case fixes)
    for wrong, correct in CADQUERY_FIXES.items():
        fixed_code = fixed_code.replace(wrong, correct)

    # Layer 2 continued: Fix syntax errors (missing dots, etc.)
    fixed_code = fix_syntax_errors(fixed_code)

    # Layer 2: Fix faces/edges with lowercase selectors
    fixed_code = re.sub(r'\.faces\(["\']>z["\']\)', '.faces(">Z")', fixed_code, flags=re.IGNORECASE)
    fixed_code = re.sub(r'\.faces\(["\']<z["\']\)', '.faces("<Z")', fixed_code, flags=re.IGNORECASE)
    fixed_code = re.sub(r'\.faces\(["\']>x["\']\)', '.faces(">X")', fixed_code, flags=re.IGNORECASE)
    fixed_code = re.sub(r'\.faces\(["\']<x["\']\)', '.faces("<X")', fixed_code, flags=re.IGNORECASE)
    fixed_code = re.sub(r'\.faces\(["\']>y["\']\)', '.faces(">Y")', fixed_code, flags=re.IGNORECASE)
    fixed_code = re.sub(r'\.faces\(["\']<y["\']\)', '.faces("<Y")', fixed_code, flags=re.IGNORECASE)

    fixed_code = re.sub(r'\.edges\(["\']\|z["\']\)', '.edges("|Z")', fixed_code, flags=re.IGNORECASE)
    fixed_code = re.sub(r'\.edges\(["\']\|x["\']\)', '.edges("|X")', fixed_code, flags=re.IGNORECASE)
    fixed_code = re.sub(r'\.edges\(["\']\|y["\']\)', '.edges("|Y")', fixed_code, flags=re.IGNORECASE)

    # Layer 3: Method whitelist validation (logs warnings but doesn't fail)
    is_valid, invalid_methods, fixed_code = validate_methods(fixed_code)
    if not is_valid:
        print(f"Warning: Found potentially invalid methods: {invalid_methods}")

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


def execute_cadquery(code: str, max_retries: int = 3) -> tuple[str, str, str]:
    """
    Execute CadQuery code and save the result as a STEP file.
    Uses comprehensive validation pipeline with multiple retry attempts.
    Returns (file_id, error_message, final_code).
    """
    import cadquery as cq
    import re

    # Apply comprehensive fixes before first attempt
    code = fix_cadquery_code(code)

    def try_execute(code_to_run):
        """Attempt to execute the code."""
        namespace = {'cq': cq}
        exec(code_to_run, namespace)

        if 'result' not in namespace:
            raise ValueError("Code must define a 'result' variable with the CadQuery object")

        return namespace['result']

    def apply_error_fix(code_to_fix: str, error_msg: str) -> str:
        """Apply fixes based on the specific error encountered."""
        fixed = code_to_fix

        # Fix "has no attribute" errors
        attr_match = re.search(r"has no attribute '(\w+)'", error_msg)
        if attr_match:
            bad_attr = attr_match.group(1)

            # Check if there's a Python suggestion in the error
            suggestion_match = re.search(r"Did you mean: '(\w+)'\?", error_msg)
            if suggestion_match:
                correct_attr = suggestion_match.group(1)
                fixed = fixed.replace(f'.{bad_attr}(', f'.{correct_attr}(')
                fixed = fixed.replace(f'.{bad_attr})', f'.{correct_attr})')
                fixed = fixed.replace(f'.{bad_attr} ', f'.{correct_attr} ')
            else:
                # Common attribute fixes without suggestion
                common_attr_fixes = {
                    'length': 'Length',
                    'center': 'Center',
                    'area': 'Area',
                    'volume': 'Volume',
                    'normal': 'Normal',
                    'addWorkplane': 'workplane',
                    'AddWorkplane': 'workplane',
                    'createBox': 'box',
                    'makeBox': 'box',
                    'createHole': 'hole',
                    'makeHole': 'hole',
                    'addHole': 'hole',
                    'createFillet': 'fillet',
                    'makeFillet': 'fillet',
                    'addFillet': 'fillet',
                    'createChamfer': 'chamfer',
                    'makeChamfer': 'chamfer',
                    'selectFace': 'faces',
                    'selectEdge': 'edges',
                }
                if bad_attr in common_attr_fixes:
                    correct_attr = common_attr_fixes[bad_attr]
                    fixed = fixed.replace(f'.{bad_attr}(', f'.{correct_attr}(')

        # Fix "object is not callable" errors - usually missing dot
        if 'is not callable' in error_msg:
            # Re-apply syntax fixes with emphasis on missing dots
            fixed = fix_syntax_errors(fixed)
            # Also check for pattern like )Workplane -> ).Workplane
            fixed = re.sub(r'\)(Workplane|cq)', r').\1', fixed)

        # Fix "unsupported operand type(s) for /: 'tuple'" - tuple used instead of args
        if 'tuple' in error_msg and ('unsupported operand' in error_msg or 'TypeError' in error_msg):
            # Re-apply syntax fixes which includes tuple unpacking
            fixed = fix_syntax_errors(fixed)

        # Fix "unexpected EOF" or "SyntaxError" - usually unbalanced parens
        if 'SyntaxError' in error_msg or 'EOF' in error_msg:
            _, _, fixed = validate_structure(fixed)

        # Fix "name 'result' is not defined" - ensure result assignment
        if "name 'result' is not defined" in error_msg:
            lines = fixed.strip().split('\n')
            for i in range(len(lines) - 1, -1, -1):
                line = lines[i].strip()
                if line and not line.startswith('#') and not line.startswith('import'):
                    if 'cq.' in line or any(m in line for m in ['.box(', '.cylinder(', '.sphere(']):
                        if not line.startswith('result'):
                            lines[i] = 'result = ' + line
                        break
            fixed = '\n'.join(lines)

        return fixed

    # Attempt execution with retries
    current_code = code
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            result = try_execute(current_code)

            # Success - generate STEP file
            file_id = str(uuid.uuid4())
            step_path = os.path.join(STEP_DIR, f"{file_id}.step")
            cq.exporters.export(result, step_path)

            return file_id, None, current_code

        except Exception as e:
            error_msg = str(e)
            last_error = error_msg

            if attempt < max_retries:
                # Try to fix based on the error
                fixed_code = apply_error_fix(current_code, error_msg)

                # Also re-run through general fixes
                fixed_code = fix_cadquery_code(fixed_code)

                if fixed_code != current_code:
                    current_code = fixed_code
                    continue  # Retry with fixed code
                else:
                    # No fixes applied, no point retrying
                    break

    return None, f"Error executing CadQuery code: {last_error}\n{traceback.format_exc()}", current_code


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
