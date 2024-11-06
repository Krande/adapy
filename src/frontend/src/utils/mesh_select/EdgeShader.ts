// EdgeShader.ts
import * as THREE from 'three';

export const EdgeShader = {
  uniforms: THREE.UniformsUtils.merge([
    THREE.UniformsLib['common'],
    THREE.UniformsLib['lights'],
    {
      diffuse: { value: new THREE.Color(0xffffff) },
      edgeColor: { value: new THREE.Color(0x000000) },
      edgeThickness: { value: 1.0 },
      edgeStrength: { value: 1.0 },
    },
  ]),

  vertexShader: `
    varying vec3 vViewPosition;
    varying vec3 vNormal;

    #include <common>
    #include <uv_pars_vertex>
    #include <displacementmap_pars_vertex>
    #include <fog_pars_vertex>
    #include <morphtarget_pars_vertex>
    #include <skinning_pars_vertex>
    #include <logdepthbuf_pars_vertex>
    #include <clipping_planes_pars_vertex>

    void main() {
      #include <uv_vertex>
      #include <beginnormal_vertex>
      #include <morphnormal_vertex>
      #include <skinbase_vertex>
      #include <skinnormal_vertex>
      #include <defaultnormal_vertex>
      vNormal = normalize( transformedNormal );

      #include <begin_vertex>
      #include <morphtarget_vertex>
      #include <skinning_vertex>
      #include <displacementmap_vertex>
      #include <project_vertex>
      #include <logdepthbuf_vertex>
      #include <clipping_planes_vertex>
      vViewPosition = - mvPosition.xyz;

      #include <worldpos_vertex>
      #include <fog_vertex>
    }
  `,

  fragmentShader: `
    uniform vec3 diffuse;
    uniform vec3 edgeColor;
    uniform float edgeThickness;
    uniform float edgeStrength;

    varying vec3 vViewPosition;
    varying vec3 vNormal;

    #include <common>
    #include <packing>
    #include <dithering_pars_fragment>
    #include <color_pars_fragment>
    #include <uv_pars_fragment>
    #include <map_pars_fragment>
    #include <alphamap_pars_fragment>
    #include <aomap_pars_fragment>
    #include <lightmap_pars_fragment>
    #include <emissivemap_pars_fragment>
    #include <bsdfs>
    #include <lights_pars_begin>
    #include <fog_pars_fragment>
    #include <shadowmap_pars_fragment>
    #include <shadowmask_pars_fragment>
    #include <specularmap_pars_fragment>

    void main() {
      #include <clipping_planes_fragment>

      vec4 diffuseColor = vec4( diffuse, 1.0 );
      ReflectedLight reflectedLight = ReflectedLight( vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ) );
      vec3 totalEmissiveRadiance = vec3( 0.0 );

      // Compute edge factor
      vec3 normal = normalize( vNormal );
      vec3 viewDir = normalize( vViewPosition );
      float edgeFactor = edgeStrength * pow( 1.0 - abs( dot( normal, viewDir ) ), edgeThickness );
      edgeFactor = clamp( edgeFactor, 0.0, 1.0 );

      // Accumulate lighting
      #include <normal_fragment_begin>
      #include <normal_fragment_maps>
      #include <emissivemap_fragment>
      #include <lights_fragment_begin>
      #include <lights_fragment_maps>
      #include <lights_fragment_end>

      vec3 outgoingLight = reflectedLight.directDiffuse + reflectedLight.indirectDiffuse + totalEmissiveRadiance;

      // Mix base color and edge color
      vec3 finalColor = mix( outgoingLight, edgeColor, edgeFactor );

      gl_FragColor = vec4( finalColor, diffuseColor.a );

      #include <tonemapping_fragment>
      #include <encodings_fragment>
      #include <fog_fragment>
      #include <premultiplied_alpha_fragment>
      #include <dithering_fragment>
    }
  `,
  lights: true,
};
