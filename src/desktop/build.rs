fn main() {
    // App commands are ACL-gated for remote origins (the viewer is served from http://127.0.0.1:PORT):
    // list them here, grant them in capabilities/viewer.json.
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(&["spacemouse_status"])),
    )
    .expect("tauri build failed");
}
