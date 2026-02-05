/**
 * 3D Viewer Module for Text-to-CAD
 * Uses Three.js and occt-import-js to load and display STEP files
 */

class CADViewer {
    constructor(containerId, canvasId) {
        this.container = document.getElementById(containerId);
        this.canvas = document.getElementById(canvasId);
        this.placeholder = document.getElementById('viewer-placeholder');

        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.controls = null;
        this.currentModel = null;
        this.occt = null;
        this.isInitialized = false;

        this.init();
    }

    async init() {
        // Initialize Three.js scene
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x1a1a2e);

        // Setup camera
        const aspect = this.container.clientWidth / this.container.clientHeight;
        this.camera = new THREE.PerspectiveCamera(45, aspect, 0.1, 10000);
        this.camera.position.set(50, 50, 50);

        // Setup renderer
        this.renderer = new THREE.WebGLRenderer({
            canvas: this.canvas,
            antialias: true,
            alpha: true
        });
        this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
        this.renderer.setPixelRatio(window.devicePixelRatio);
        this.renderer.shadowMap.enabled = true;
        this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;

        // Setup controls
        this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.05;
        this.controls.screenSpacePanning = true;
        this.controls.minDistance = 1;
        this.controls.maxDistance = 1000;

        // Add lights
        this.setupLights();

        // Add grid helper
        this.addGrid();

        // Handle window resize
        window.addEventListener('resize', () => this.onResize());

        // Initialize OCCT library
        await this.initOCCT();

        // Start animation loop
        this.animate();

        this.isInitialized = true;
    }

    async initOCCT() {
        try {
            // Initialize occt-import-js
            this.occt = await occtimportjs();
            console.log('OCCT library initialized successfully');
        } catch (error) {
            console.error('Failed to initialize OCCT library:', error);
        }
    }

    setupLights() {
        // Ambient light
        const ambientLight = new THREE.AmbientLight(0xffffff, 0.4);
        this.scene.add(ambientLight);

        // Main directional light
        const mainLight = new THREE.DirectionalLight(0xffffff, 0.8);
        mainLight.position.set(50, 100, 50);
        mainLight.castShadow = true;
        mainLight.shadow.mapSize.width = 2048;
        mainLight.shadow.mapSize.height = 2048;
        this.scene.add(mainLight);

        // Fill light
        const fillLight = new THREE.DirectionalLight(0xffffff, 0.3);
        fillLight.position.set(-50, 50, -50);
        this.scene.add(fillLight);

        // Rim light
        const rimLight = new THREE.DirectionalLight(0xffffff, 0.2);
        rimLight.position.set(0, -50, 50);
        this.scene.add(rimLight);
    }

    addGrid() {
        // Ground plane grid
        const gridHelper = new THREE.GridHelper(100, 20, 0x444466, 0x333355);
        gridHelper.position.y = -0.01;
        this.scene.add(gridHelper);

        // Axes helper (small)
        const axesHelper = new THREE.AxesHelper(10);
        axesHelper.position.set(-45, 0.1, -45);
        this.scene.add(axesHelper);
    }

    animate() {
        requestAnimationFrame(() => this.animate());
        this.controls.update();
        this.renderer.render(this.scene, this.camera);
    }

    onResize() {
        const width = this.container.clientWidth;
        const height = this.container.clientHeight;

        this.camera.aspect = width / height;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(width, height);
    }

    async loadSTEP(url) {
        if (!this.occt) {
            throw new Error('OCCT library not initialized');
        }

        // Show loading state
        this.showCanvas();

        // Clear previous model
        this.clearModel();

        try {
            // Fetch STEP file
            const response = await fetch(url);
            if (!response.ok) {
                throw new Error(`Failed to fetch STEP file: ${response.statusText}`);
            }

            const buffer = await response.arrayBuffer();
            const fileBuffer = new Uint8Array(buffer);

            // Parse STEP file using occt-import-js
            const result = this.occt.ReadStepFile(fileBuffer, null);

            if (!result.success) {
                throw new Error('Failed to parse STEP file');
            }

            // Create mesh from parsed data
            const meshes = this.createMeshesFromOCCT(result);

            // Add meshes to scene
            const group = new THREE.Group();
            meshes.forEach(mesh => group.add(mesh));

            this.currentModel = group;
            this.scene.add(this.currentModel);

            // Fit camera to model
            this.fitCameraToModel();

            return true;
        } catch (error) {
            console.error('Error loading STEP file:', error);
            throw error;
        }
    }

    createMeshesFromOCCT(result) {
        const meshes = [];

        // Material for the model
        const material = new THREE.MeshPhysicalMaterial({
            color: 0x4a90d9,
            metalness: 0.1,
            roughness: 0.5,
            clearcoat: 0.3,
            clearcoatRoughness: 0.2,
            side: THREE.DoubleSide
        });

        // Edge material
        const edgeMaterial = new THREE.LineBasicMaterial({
            color: 0x000000,
            linewidth: 1
        });

        // Process each mesh in the result
        for (const mesh of result.meshes) {
            // Create geometry
            const geometry = new THREE.BufferGeometry();

            // Set vertex positions
            geometry.setAttribute(
                'position',
                new THREE.Float32BufferAttribute(mesh.attributes.position.array, 3)
            );

            // Set normals if available
            if (mesh.attributes.normal) {
                geometry.setAttribute(
                    'normal',
                    new THREE.Float32BufferAttribute(mesh.attributes.normal.array, 3)
                );
            } else {
                geometry.computeVertexNormals();
            }

            // Set indices
            if (mesh.index) {
                geometry.setIndex(new THREE.BufferAttribute(mesh.index.array, 1));
            }

            // Create mesh
            const threeMesh = new THREE.Mesh(geometry, material.clone());
            threeMesh.castShadow = true;
            threeMesh.receiveShadow = true;

            // Add edges for better visualization
            const edges = new THREE.EdgesGeometry(geometry, 15);
            const edgeLines = new THREE.LineSegments(edges, edgeMaterial);
            threeMesh.add(edgeLines);

            meshes.push(threeMesh);
        }

        return meshes;
    }

    fitCameraToModel() {
        if (!this.currentModel) return;

        // Calculate bounding box
        const box = new THREE.Box3().setFromObject(this.currentModel);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());

        // Calculate camera distance
        const maxDim = Math.max(size.x, size.y, size.z);
        const fov = this.camera.fov * (Math.PI / 180);
        let cameraDistance = maxDim / (2 * Math.tan(fov / 2));
        cameraDistance *= 2; // Add some padding

        // Position camera
        const direction = new THREE.Vector3(1, 0.8, 1).normalize();
        this.camera.position.copy(center).add(direction.multiplyScalar(cameraDistance));
        this.camera.lookAt(center);

        // Update controls target
        this.controls.target.copy(center);
        this.controls.update();
    }

    clearModel() {
        if (this.currentModel) {
            this.scene.remove(this.currentModel);
            this.currentModel.traverse((child) => {
                if (child.geometry) {
                    child.geometry.dispose();
                }
                if (child.material) {
                    if (Array.isArray(child.material)) {
                        child.material.forEach(m => m.dispose());
                    } else {
                        child.material.dispose();
                    }
                }
            });
            this.currentModel = null;
        }
    }

    resetView() {
        if (this.currentModel) {
            this.fitCameraToModel();
        } else {
            this.camera.position.set(50, 50, 50);
            this.camera.lookAt(0, 0, 0);
            this.controls.target.set(0, 0, 0);
            this.controls.update();
        }
    }

    showCanvas() {
        this.canvas.style.display = 'block';
        if (this.placeholder) {
            this.placeholder.style.display = 'none';
        }
        this.onResize();
    }

    hideCanvas() {
        this.canvas.style.display = 'none';
        if (this.placeholder) {
            this.placeholder.style.display = 'block';
        }
    }

    setModelColor(color) {
        if (this.currentModel) {
            this.currentModel.traverse((child) => {
                if (child.isMesh && child.material) {
                    child.material.color.set(color);
                }
            });
        }
    }

    takeScreenshot() {
        this.renderer.render(this.scene, this.camera);
        return this.canvas.toDataURL('image/png');
    }
}

// Export for use in app.js
window.CADViewer = CADViewer;
