fn main() {
    // App commands are ACL-gated for remote origins (the viewer is served from http://127.0.0.1:PORT or a
    // hosted server): list them here; main.rs grants them at startup, to the viewer's origin only, with a
    // runtime capability (there is no static capabilities/ file: the origin is not known at build time).
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(&["spacemouse_status"])),
    )
    .expect("tauri build failed");
}
