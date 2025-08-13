import * as THREE from "three";

// Creates a ShaderMaterial that renders points as circular, shaded sphere impostors.
// It supports a uniform pointSize (in pixels, consistent with PointsMaterial) and a base color.
export function createSphericalPointMaterial(params: {
    pointSize: number;
    color?: THREE.Color | number | string;
    opacity?: number;
    useVertexColors?: boolean;
    depthTest?: boolean;
    depthWrite?: boolean;
}): THREE.ShaderMaterial {
    const {
        pointSize,
        color = 0xffffff,
        opacity = 1.0,
        useVertexColors = false,
        depthTest = true,
        depthWrite = false,
    } = params;

    const uColor = new THREE.Color(color as any);

    const vertex = `
        precision mediump float;
        #ifdef USE_COLOR
        attribute vec3 color;
        varying vec3 vColor;
        #endif
        uniform float pointSize; // in pixels
        void main() {
            vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
            gl_Position = projectionMatrix * mvPosition;
            // Keep size semantics similar to PointsMaterial: in pixels
            gl_PointSize = pointSize;
            #ifdef USE_COLOR
            vColor = color;
            #endif
        }
    `;

    const fragment = `
        precision mediump float;
        #ifdef USE_COLOR
        varying vec3 vColor;
        #endif
        uniform vec3 uColor;
        uniform float uOpacity;
        void main() {
            // gl_PointCoord in [0,1]; remap to [-1,1] for a unit disk
            vec2 p = gl_PointCoord * 2.0 - 1.0;
            float r2 = dot(p, p);
            if (r2 > 1.0) discard; // circular mask

            // Approximate a 3D sphere normal for simple lighting
            float z = sqrt(1.0 - r2);
            vec3 normal = normalize(vec3(p.x, p.y, z));

            // Simple directional light for spherical shading
            vec3 lightDir = normalize(vec3(0.3, 0.4, 1.0));
            float diff = clamp(dot(normal, lightDir), 0.0, 1.0);
            float ambient = 0.3;
            float lighting = ambient + 0.7 * diff;

            vec3 base = 
            #ifdef USE_COLOR
                vColor;
            #else
                uColor;
            #endif

            vec3 col = base * lighting;
            gl_FragColor = vec4(col, uOpacity);
        }
    `;

    const mat = new THREE.ShaderMaterial({
        vertexShader: vertex,
        fragmentShader: fragment,
        uniforms: {
            pointSize: { value: pointSize },
            uColor: { value: uColor },
            uOpacity: { value: opacity },
        },
        transparent: opacity < 1.0,
        depthTest,
        depthWrite,
    });

    if (useVertexColors) {
        mat.defines = { ...(mat.defines || {}), USE_COLOR: 1 } as any;
    }

    return mat;
}

// Replace a THREE.Points' material with the spherical impostor material, preserving color and size when possible.
export function applySphericalImpostor(points: THREE.Points, defaultSize: number) {
    const geom = points.geometry as THREE.BufferGeometry;

    // Decide if vertex colors exist
    const hasVertexColors = !!geom.getAttribute('color');

    // Extract size and color from existing material if possible
    let size = defaultSize;
    let baseColor: THREE.Color | number | string = 0xffffff;
    let opacity = 1.0;

    const mat = points.material as THREE.Material | THREE.Material[];

    const takeFrom = (m: THREE.Material) => {
        if ((m as any).isPointsMaterial) {
            const pm = m as THREE.PointsMaterial;
            size = (typeof pm.size === 'number') ? pm.size : size;
            baseColor = pm.color || baseColor;
            opacity = (typeof (pm as any).opacity === 'number') ? (pm as any).opacity : opacity;
        } else if ((m as any).isShaderMaterial) {
            const sm = m as THREE.ShaderMaterial & { uniforms?: any };
            if (sm.uniforms) {
                if (sm.uniforms.pointSize && typeof sm.uniforms.pointSize.value === 'number') {
                    size = sm.uniforms.pointSize.value;
                }
                if (sm.uniforms.uColor && sm.uniforms.uColor.value) {
                    baseColor = sm.uniforms.uColor.value;
                }
                if (sm.uniforms.uOpacity && typeof sm.uniforms.uOpacity.value === 'number') {
                    opacity = sm.uniforms.uOpacity.value;
                }
            }
        }
    };

    if (Array.isArray(mat)) mat.forEach(takeFrom); else if (mat) takeFrom(mat);

    // Create the spherical impostor material
    const sphereMat = createSphericalPointMaterial({
        pointSize: size,
        color: baseColor,
        opacity,
        useVertexColors: hasVertexColors,
        depthTest: false,
        depthWrite: false,
    });

    points.material = sphereMat;
}
