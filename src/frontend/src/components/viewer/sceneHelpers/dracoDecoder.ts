// KHR_draco_mesh_compression for the model loader. three.js' DRACOLoader fetches its decoder from
// a path it is given, usually a CDN; the viewer is served as one self-contained bundle, so the
// decoder is handed to it from the bundle instead (./dracoDecoderFiles.ts). That file is only
// evaluated when a model that uses the extension is first decoded.
import {DRACOLoader} from "three/examples/jsm/loaders/DRACOLoader";

class BundledDracoLoader extends DRACOLoader {
    constructor() {
        super();
        // The JavaScript build of the decoder: the one the bundle carries.
        this.setDecoderConfig({type: "js"});
    }

    /** What DRACOLoader asks for by file name: the decoder script, as text. */
    _loadLibrary(): Promise<string> {
        return import("./dracoDecoderFiles").then((files) => files.dracoDecoderSource);
    }
}

let shared: DRACOLoader | null = null;

/** The one Draco loader every model load shares: its workers are started on first use and kept. */
export function dracoLoader(): DRACOLoader {
    return (shared ??= new BundledDracoLoader());
}
