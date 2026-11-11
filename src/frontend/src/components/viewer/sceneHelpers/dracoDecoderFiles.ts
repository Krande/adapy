// The Draco decoder, taken into the bundle from three.js' own copy (the glTF build: the attributes
// a glTF mesh has, and no more). As source text, so it is part of the script the page already has
// and nothing is fetched from anywhere when a compressed model loads.
//
// This is the JavaScript build of the decoder. The WebAssembly build decodes faster, but it is a
// binary beside a script, and every build of the viewer (the single-file bundle among them) would
// need to be taught to inline it; the JavaScript build is text in all of them.
import decoderSource from "three/examples/jsm/libs/draco/gltf/draco_decoder.js?raw";

/** The decoder as source text: it is run in a worker. */
export const dracoDecoderSource: string = decoderSource;
