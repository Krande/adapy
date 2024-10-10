import * as THREE from 'three';

function custom_shader() {
    // Vertex and fragment shaders for rendering lines with custom line width
    const lineVertexShader = `
  precision mediump float;
  attribute vec3 position;
  uniform mat4 projectionMatrix;
  uniform mat4 modelViewMatrix;
  void main() {
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

    const lineFragmentShader = `
  precision mediump float;
  uniform float lineWidth;
  void main() {
    gl_FragColor = vec4(1.0, 0.5, 0.0, 1.0); // Orange color
    // Simulate line width by varying the fragment's alpha based on the lineWidth
    gl_FragColor.a *= lineWidth; // Modify alpha to control line visibility
  }
`;

// Vertex and fragment shaders for rendering vertices as points
    const pointVertexShader = `
  precision mediump float;
  attribute vec3 position;
  uniform mat4 projectionMatrix;
  uniform mat4 modelViewMatrix;
  uniform float pointSize;
  void main() {
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = pointSize; // Control the size of the vertex points
  }
`;

    const pointFragmentShader = `
  precision mediump float;
  void main() {
    gl_FragColor = vec4(0.0, 1.0, 0.0, 1.0); // Green color for vertices
  }
`;

// Create vertices of a basic triangle face
    const vertices = new Float32Array([
        0, 0, 0, // Vertex 1
        1, 0, 0, // Vertex 2
        0.5, 1, 0, // Vertex 3
    ]);

// Define the pairs of vertices that form line segments
    const lineIndices = new Uint16Array([
        0, 1, // Line between Vertex 1 and Vertex 2
        1, 2, // Line between Vertex 2 and Vertex 3
        2, 0, // Line between Vertex 3 and Vertex 1
    ]);

// Create a buffer geometry for the lines and add attributes for vertices and line indices
    const lineGeometry = new THREE.BufferGeometry();
    lineGeometry.setAttribute('position', new THREE.BufferAttribute(vertices, 3));
    lineGeometry.setIndex(new THREE.BufferAttribute(lineIndices, 1));

// Create a shader material for the lines with adjustable line width
    const lineMaterial = new THREE.ShaderMaterial({
        vertexShader: lineVertexShader,
        fragmentShader: lineFragmentShader,
        uniforms: {
            lineWidth: {value: 1.0}, // Uniform for controlling line width
        },
        transparent: true, // Enable transparency for line visibility control
    });

// Create a LineSegments object using the geometry and material
    const lineSegments = new THREE.LineSegments(lineGeometry, lineMaterial);

// Create a separate geometry for points/vertices
    const pointGeometry = new THREE.BufferGeometry();
    pointGeometry.setAttribute('position', new THREE.BufferAttribute(vertices, 3));

// Create a shader material for points/vertices with adjustable point size
    const pointMaterial = new THREE.ShaderMaterial({
        vertexShader: pointVertexShader,
        fragmentShader: pointFragmentShader,
        uniforms: {
            pointSize: {value: 5.0}, // Uniform for controlling point size
        },
    });

// Create a Points object to render vertices
    const points = new THREE.Points(pointGeometry, pointMaterial);

// Add the line segments and points to the scene
    const scene = new THREE.Scene();
    scene.add(lineSegments);
    scene.add(points);

// Create a camera and renderer
    const camera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 1000);
    camera.position.z = 2;

    const renderer = new THREE.WebGLRenderer();
    renderer.setSize(window.innerWidth, window.innerHeight);
    document.body.appendChild(renderer.domElement);

// Animation loop
    const animate = () => {
        requestAnimationFrame(animate);
        renderer.render(scene, camera);
    };

    animate();

}
