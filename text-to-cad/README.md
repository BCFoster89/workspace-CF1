# Text-to-CAD

A browser-based application that converts natural language descriptions into 3D CAD models using AI.

## Features

- **Natural Language Input**: Describe your 3D model in plain English
- **AI-Powered Code Generation**: Uses Claude to convert descriptions to CadQuery code
- **Real-time 3D Viewer**: View generated models in the browser using Three.js
- **STEP File Export**: Download models in industry-standard STEP format
- **Code Editor**: View and modify the generated CadQuery code

## Architecture

```
text-to-cad/
├── app.py                 # Flask backend server
├── requirements.txt       # Python dependencies
├── .env.example          # Environment variables template
└── static/
    ├── index.html        # Main HTML page
    ├── css/
    │   └── style.css     # Application styles
    └── js/
        ├── app.js        # Main application logic
        └── viewer.js     # Three.js 3D viewer
```

## Prerequisites

- Python 3.9+
- An Anthropic API key

## Installation

1. Clone the repository and navigate to the text-to-cad directory:
   ```bash
   cd text-to-cad
   ```

2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Set up your environment variables:
   ```bash
   cp .env.example .env
   # Edit .env and add your Anthropic API key
   ```

5. Run the application:
   ```bash
   export ANTHROPIC_API_KEY=your_api_key_here
   python app.py
   ```

6. Open your browser to `http://localhost:5000`

## Usage

1. Type a description of the 3D model you want to create in the chat box
2. Click "Generate" or press Enter
3. The AI will convert your description to CadQuery code
4. The resulting 3D model will be displayed in the viewer
5. Use the "Download STEP" button to save the file

### Example Prompts

- "Create a cube with 5mm sides and a 3mm hole in the middle"
- "A cylinder with 10mm radius and 30mm height"
- "A rounded rectangular box 40x20x15mm with 3mm fillets"
- "A simple bracket with two mounting holes"
- "A gear with 20 teeth, 2mm module, and 10mm thickness"

## Viewer Controls

- **Rotate**: Left-click and drag
- **Pan**: Right-click and drag (or Shift + left-click)
- **Zoom**: Mouse wheel

## API Endpoints

### POST /api/generate
Generate a CAD model from a text description.

**Request:**
```json
{
  "description": "a cube with 10mm sides"
}
```

**Response:**
```json
{
  "success": true,
  "code": "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)",
  "file_id": "uuid-string"
}
```

### POST /api/execute
Execute CadQuery code directly.

**Request:**
```json
{
  "code": "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)"
}
```

### GET /api/step/{file_id}
Download or view a STEP file.

Query parameters:
- `download=true` - Force download with filename

## Technology Stack

- **Backend**: Python, Flask, CadQuery
- **Frontend**: HTML, CSS, JavaScript
- **3D Viewer**: Three.js, occt-import-js
- **AI**: Claude (Anthropic)

## Troubleshooting

### "OCCT library not initialized"
The occt-import-js library may take a moment to load. Wait a few seconds and try again.

### "ANTHROPIC_API_KEY not set"
Make sure you've set the environment variable:
```bash
export ANTHROPIC_API_KEY=your_key_here
```

### Model not displaying
- Check the browser console for errors
- Ensure the STEP file was generated successfully
- Try refreshing the page

## License

MIT License
