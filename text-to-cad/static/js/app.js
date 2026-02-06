/**
 * Text-to-CAD Application
 * Main application logic for chat interface and API communication
 */

class TextToCADApp {
    constructor() {
        // DOM Elements
        this.chatMessages = document.getElementById('chat-messages');
        this.chatInput = document.getElementById('chat-input');
        this.sendBtn = document.getElementById('send-btn');
        this.clearChatBtn = document.getElementById('clear-chat');
        this.resetViewBtn = document.getElementById('reset-view');
        this.downloadBtn = document.getElementById('download-step');
        this.loadingOverlay = document.getElementById('loading-overlay');
        this.loadingText = document.getElementById('loading-text');
        this.codePanel = document.getElementById('code-panel');
        this.codeToggle = document.getElementById('code-toggle');
        this.codeContent = document.getElementById('code-content');
        this.generatedCode = document.getElementById('generated-code');
        this.runCodeBtn = document.getElementById('run-code');

        // State
        this.currentFileId = null;
        this.currentCode = null;  // Track current CadQuery code for iterative building
        this.conversationHistory = [];
        this.viewer = null;

        // API base URL (same origin)
        this.apiBase = '';

        // Initialize
        this.init();
    }

    async init() {
        // Initialize 3D viewer
        this.viewer = new CADViewer('viewer-container', 'viewer-canvas');

        // Setup event listeners
        this.setupEventListeners();

        // Focus on input
        this.chatInput.focus();
    }

    setupEventListeners() {
        // Send button click
        this.sendBtn.addEventListener('click', () => this.sendMessage());

        // Enter key to send (Shift+Enter for new line)
        this.chatInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Clear chat
        this.clearChatBtn.addEventListener('click', () => this.clearChat());

        // Reset view
        this.resetViewBtn.addEventListener('click', () => this.viewer.resetView());

        // Download STEP
        this.downloadBtn.addEventListener('click', () => this.downloadSTEP());

        // Code panel toggle
        this.codeToggle.addEventListener('click', () => {
            this.codePanel.classList.toggle('collapsed');
        });

        // Run modified code
        this.runCodeBtn.addEventListener('click', () => this.runModifiedCode());
    }

    async sendMessage() {
        const message = this.chatInput.value.trim();
        if (!message) return;

        // Clear input
        this.chatInput.value = '';

        // Add user message to chat
        this.addMessage('user', message);

        // Show loading - indicate if we're modifying existing model
        if (this.currentCode) {
            this.showLoading('Modifying your 3D model...');
        } else {
            this.showLoading('Generating your 3D model...');
        }

        try {
            // Call generate API with previous code for iterative building
            const requestBody = { description: message };
            if (this.currentCode) {
                requestBody.previous_code = this.currentCode;
            }

            const response = await fetch(`${this.apiBase}/api/generate`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(requestBody)
            });

            const data = await response.json();

            if (data.success) {
                // Update code panel and store current code for next iteration
                this.updateCodePanel(data.code);
                this.currentCode = data.code;

                // Store file ID
                this.currentFileId = data.file_id;

                // Enable download button
                this.downloadBtn.disabled = false;

                // Load model in viewer
                this.showLoading('Loading 3D model...');
                await this.viewer.loadSTEP(`${this.apiBase}/api/step/${data.file_id}`);

                // Add success message
                const successMsg = this.currentCode ?
                    'Model updated! Continue describing changes or click "Clear" to start fresh.' :
                    'Model generated! You can now describe modifications to build on this model.';
                this.addMessage('assistant', successMsg);

            } else {
                // Show error
                const errorMsg = data.error || 'Failed to generate model';
                this.addMessage('error', errorMsg);

                // Show code if available (for debugging)
                if (data.code) {
                    this.updateCodePanel(data.code);
                }
            }
        } catch (error) {
            console.error('Error:', error);
            this.addMessage('error', `Connection error: ${error.message}. Make sure the server is running.`);
        } finally {
            this.hideLoading();
        }
    }

    async runModifiedCode() {
        const code = this.generatedCode.textContent;
        if (!code || code === 'No code generated yet') {
            this.addMessage('error', 'No code to run');
            return;
        }

        this.showLoading('Running modified code...');

        try {
            const response = await fetch(`${this.apiBase}/api/execute`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ code })
            });

            const data = await response.json();

            if (data.success) {
                // Update current code to the returned (possibly auto-fixed) version
                const finalCode = data.code || code;
                this.currentCode = finalCode;
                this.currentFileId = data.file_id;
                this.downloadBtn.disabled = false;

                // Update code panel if it was auto-fixed
                if (data.code && data.code !== code) {
                    this.updateCodePanel(data.code);
                    this.addMessage('assistant', 'Code was auto-corrected and model updated! Future changes will build on this version.');
                } else {
                    this.addMessage('assistant', 'Model updated from modified code! Future changes will build on this version.');
                }

                this.showLoading('Loading updated model...');
                await this.viewer.loadSTEP(`${this.apiBase}/api/step/${data.file_id}`);
            } else {
                this.addMessage('error', data.error || 'Failed to execute code');
            }
        } catch (error) {
            this.addMessage('error', `Error: ${error.message}`);
        } finally {
            this.hideLoading();
        }
    }

    addMessage(type, content) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${type}`;

        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';

        // Handle different content types
        if (typeof content === 'string') {
            // Simple text content
            const p = document.createElement('p');
            p.textContent = content;
            contentDiv.appendChild(p);
        } else {
            // Rich content (object with text and possibly code)
            if (content.text) {
                const p = document.createElement('p');
                p.textContent = content.text;
                contentDiv.appendChild(p);
            }
            if (content.code) {
                const pre = document.createElement('pre');
                const code = document.createElement('code');
                code.textContent = content.code;
                pre.appendChild(code);
                contentDiv.appendChild(pre);
            }
        }

        messageDiv.appendChild(contentDiv);
        this.chatMessages.appendChild(messageDiv);

        // Scroll to bottom
        this.chatMessages.scrollTop = this.chatMessages.scrollHeight;

        // Track conversation for context
        if (type === 'user') {
            this.conversationHistory.push({ role: 'user', content: typeof content === 'string' ? content : content.text });
        } else if (type === 'assistant') {
            this.conversationHistory.push({ role: 'assistant', content: typeof content === 'string' ? content : content.text });
        }
    }

    updateCodePanel(code) {
        // Make code editable
        this.generatedCode.textContent = code;
        this.generatedCode.contentEditable = true;
        this.runCodeBtn.disabled = false;

        // Apply basic syntax highlighting (simple version)
        this.highlightCode();

        // Expand code panel if collapsed
        this.codePanel.classList.remove('collapsed');
    }

    highlightCode() {
        // Very basic syntax highlighting - for production, use a library like Prism.js
        const code = this.generatedCode.textContent;

        // Keywords
        const keywords = ['import', 'as', 'from', 'def', 'return', 'if', 'else', 'for', 'while', 'True', 'False', 'None'];

        // For now, just keep it as plain text
        // In production, use Prism.js or highlight.js
    }

    clearChat() {
        // Keep only the welcome message
        const welcome = this.chatMessages.querySelector('.message.system');
        this.chatMessages.innerHTML = '';
        if (welcome) {
            this.chatMessages.appendChild(welcome);
        }

        // Clear conversation history and current model code
        this.conversationHistory = [];
        this.currentCode = null;  // Reset for new model

        // Reset code panel
        this.generatedCode.textContent = 'No code generated yet';
        this.runCodeBtn.disabled = true;

        // Disable download
        this.downloadBtn.disabled = true;
        this.currentFileId = null;

        // Clear viewer
        this.viewer.clearModel();
        this.viewer.hideCanvas();

        // Add confirmation message
        this.addMessage('assistant', 'Chat cleared. Ready to create a new model!');
    }

    downloadSTEP() {
        if (!this.currentFileId) {
            this.addMessage('error', 'No model to download');
            return;
        }

        // Create download link
        const link = document.createElement('a');
        link.href = `${this.apiBase}/api/step/${this.currentFileId}?download=true`;
        link.download = `model-${this.currentFileId.slice(0, 8)}.step`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }

    showLoading(text = 'Loading...') {
        this.loadingText.textContent = text;
        this.loadingOverlay.classList.add('active');
    }

    hideLoading() {
        this.loadingOverlay.classList.remove('active');
    }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.app = new TextToCADApp();
});
