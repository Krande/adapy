// vite.config.embed-webgpu.ts: adacpp_render.{js,wasm} inlined as base64.
declare module "virtual:adacpp-render-inline" {
    const inline: {js: string; wasm: string};
    export default inline;
}
