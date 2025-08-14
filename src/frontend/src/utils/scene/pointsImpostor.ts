import * as THREE from "three";

// Creates a ShaderMaterial that renders points as circular, shaded sphere impostors.
// It supports both screen-space sizing (pointSize in pixels) and absolute world-space sizing
// via uniforms. When uWorldSize is true, the shader converts a world-space diameter (uWorldPointSize)
// into pixels using the camera FOV and viewport height.
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
        depthWrite = true,
    } = params;

    const uColor = new THREE.Color(color as any);

    const vertex = `
        precision mediump float;
        #include <common>
        #ifdef USE_COLOR
        #include <color_pars_vertex>
        #endif
        #include <morphtarget_pars_vertex>
        uniform float pointSize; // screen-space size (pixels)
        uniform bool uWorldSize; // toggle for absolute sizing
        uniform float uWorldPointSize; // world-space diameter
        uniform float uFov; // degrees
        uniform float uViewportHeight; // pixels
        void main() {
            vec3 transformed = position;
            #include <morphtarget_vertex>
            vec4 mvPosition = modelViewMatrix * vec4(transformed, 1.0);
            gl_Position = projectionMatrix * mvPosition;

            float pixelSize = pointSize;
            if (uWorldSize) {
                // Convert world diameter to pixel diameter: scale / -mvPosition.z
                float scale = uViewportHeight / (2.0 * tan(radians(uFov) * 0.5));
                pixelSize = uWorldPointSize * scale / -mvPosition.z;
            }
            gl_PointSize = pixelSize;

            #ifdef USE_COLOR
            #include <color_vertex>
            #endif
        }
    `;

    const fragment = `
        precision mediump float;
        #include <common>
        #ifdef USE_COLOR
        #include <color_pars_fragment>
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
            #include <tonemapping_fragment>
            #include <colorspace_fragment>
        }
    `;

    const mat = new THREE.ShaderMaterial({
        vertexShader: vertex,
        fragmentShader: fragment,
        uniforms: {
            pointSize: { value: pointSize },
            uWorldSize: { value: false },
            uWorldPointSize: { value: 1.0 },
            uFov: { value: 50.0 },
            uViewportHeight: { value: 800.0 },
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

    // Decide if vertex colors exist and are meaningful (non-zero)
    const colorAttr = geom.getAttribute('color') as THREE.BufferAttribute | undefined;
    let hasVertexColors = false;
    if (colorAttr) {
        const arr = colorAttr.array as ArrayLike<number>;
        const len = colorAttr.itemSize * colorAttr.count; // usually 3 * N
        const step = Math.max(1, Math.floor((len / colorAttr.itemSize) / 1000)); // sample up to ~1000 vertices
        // Scan a subset for any non-zero color component
        outer: for (let i = 0; i < colorAttr.count; i += step) {
            const idx = i * colorAttr.itemSize;
            const r = arr[idx];
            const g = arr[idx + 1];
            const b = arr[idx + 2];
            if ((r ?? 0) > 0 || (g ?? 0) > 0 || (b ?? 0) > 0) {
                hasVertexColors = true;
                break outer;
            }
        }
    }

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
        depthTest: true,
        depthWrite: true,
    });

    // Enable morph targets on material if geometry provides morph attributes
    const hasMorphs = !!geom.morphAttributes && Array.isArray(geom.morphAttributes.position) && geom.morphAttributes.position.length > 0;
    if (hasMorphs) {
        (sphereMat as any).morphTargets = true;
        sphereMat.needsUpdate = true;
    }

    points.material = sphereMat;
}
