//! ada-desktop: a native window shell for the adapy local viewer.
//!
//! It embeds no frontend. `ada-desktop --url http://127.0.0.1:PORT/` opens a window on a viewer that
//! `ada desktop` (or anything else) already started; `ada desktop` is the normal way to run it. With
//! `--trust-origin ORIGIN` the viewer may instead be a hosted deployment (`ada desktop --server`). Pop-outs
//! (`window.open`) become native windows of the same webview environment, so BroadcastChannel and the
//! shared storage (localStorage, IndexedDB, OPFS) keep working between them.
//!
//! Security model, in short (src/desktop/README.md has the long form):
//! * every window may only show the viewer's own origin (the one `--url` names: loopback http, or the
//!   origin named by `--trust-origin`) and the identity provider's sign-in pages (`--auth-origin`, the
//!   built-in Entra ID hosts, and any https page a sign-in passes through, see origin.rs); any other
//!   navigation is cancelled and http(s)/mailto links open in the system browser;
//! * IPC is granted to the viewer's origin only, added at startup as a runtime capability, and only for
//!   the 3D mouse events and `spacemouse_status`: no file system, shell or window commands are exposed.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod origin;
mod spacemouse;

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, OnceLock};
use tauri::ipc::CapabilityBuilder;
use tauri::webview::{NewWindowFeatures, NewWindowResponse, WebviewWindowBuilder};
use tauri::{AppHandle, Emitter, Manager, Url, WebviewUrl};
use tauri_plugin_window_state::{AppHandleExt, StateFlags, WindowExt};

const USAGE: &str = "\
usage: ada-desktop --url URL [options]

Opens the adapy viewer at URL in a native window. Normally started by `ada desktop`.

options:
  --url URL          the viewer to open; http on 127.0.0.1, localhost or [::1], unless --trust-origin
  --trust-origin O   open a remote server: O is its origin (https://host[:port]) and the only one the
                     windows show and the only one granted native events; URL must be on it
  --auth-origin O    an identity provider's sign-in origin windows may navigate to (not trusted, no
                     native events); repeatable
  --insecure         accept plain http for --trust-origin / --auth-origin (lab setups)
  --no-sso           do not sign in with the Windows account (WebView2's
                     AllowSingleSignOnUsingOSPrimaryAccount, on by default)
  --title TITLE      main window title (default: Ada Studio)
  --native-drop      let the shell take dropped files (native paths) instead of the page (File objects)
  --no-spacemouse    do not read the 3D mouse natively; the page then sees WebView2's own WebHID
  --spacemouse-log   no window: list 3D mouse HID interfaces and print what the device sends
  --version          print the version (and the WebView version) and exit
  -h, --help         this text";

/// Exit code when the platform webview is missing or the window cannot be created.
const EXIT_NO_WEBVIEW: i32 = 3;

static SM_OPEN: AtomicBool = AtomicBool::new(false);
static POPUPS: AtomicUsize = AtomicUsize::new(0);
/// Which origins windows may show / open on / be granted IPC.
static POLICY: OnceLock<origin::Policy> = OnceLock::new();

#[derive(Clone)]
struct Opts {
    native_drop: bool,
    spacemouse: bool,
    sso: bool,
}

/// Log a navigation decision that is not plain app-internal (host and path only: sign-in URLs carry
/// codes, state and login hints).
fn log_nav(label: &str, what: &str, url: &Url) {
    eprintln!("[ada-desktop] {label}: {what} {}", origin::redact(url));
}

/// Whether the native reader has a 3D mouse open (status events may fire before a page has loaded).
#[tauri::command]
fn spacemouse_status() -> bool {
    SM_OPEN.load(Ordering::Relaxed)
}

/// A GUI-subsystem exe has no console: borrow the parent's, so `--version`, `--help` and log lines
/// show up in the terminal `ada desktop` was started from. Fails quietly when there is none, and is
/// skipped when stdout was redirected (to a file or pipe), which already works.
#[cfg(windows)]
fn attach_parent_console() {
    extern "system" {
        fn AttachConsole(pid: u32) -> i32;
        fn GetStdHandle(which: u32) -> isize;
    }
    unsafe {
        let out = GetStdHandle(-11i32 as u32); // STD_OUTPUT_HANDLE
        if out == 0 || out == -1 {
            AttachConsole(u32::MAX); // ATTACH_PARENT_PROCESS
        }
    }
}

#[cfg(windows)]
fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

/// Print and, on Windows, show a message box (a double-clicked exe has no console), then exit.
fn fatal(msg: &str) -> ! {
    eprintln!("ada-desktop: {msg}");
    #[cfg(windows)]
    {
        #[link(name = "user32")]
        extern "system" {
            fn MessageBoxW(hwnd: *mut std::ffi::c_void, text: *const u16, caption: *const u16, kind: u32) -> i32;
        }
        let (text, caption) = (wide(msg), wide("ada-desktop"));
        unsafe {
            MessageBoxW(std::ptr::null_mut(), text.as_ptr(), caption.as_ptr(), 0x10); // MB_ICONERROR
        }
    }
    std::process::exit(EXIT_NO_WEBVIEW);
}

/// Hand a link the windows may not show to the system browser. Only http(s) and mailto: anything else
/// (file:, custom schemes) is dropped, so a page cannot make the shell launch arbitrary handlers.
fn open_external(url: &Url) {
    if !matches!(url.scheme(), "http" | "https" | "mailto") {
        eprintln!("[ada-desktop] blocked navigation to {url}");
        return;
    }
    let target = url.to_string();
    // Off the UI thread: the handler that calls this runs inside a webview callback.
    std::thread::spawn(move || {
        #[cfg(windows)]
        {
            #[link(name = "shell32")]
            extern "system" {
                fn ShellExecuteW(
                    hwnd: *mut std::ffi::c_void,
                    op: *const u16,
                    file: *const u16,
                    params: *const u16,
                    dir: *const u16,
                    show: i32,
                ) -> isize;
            }
            let (op, file) = (wide("open"), wide(&target));
            unsafe {
                ShellExecuteW(std::ptr::null_mut(), op.as_ptr(), file.as_ptr(), std::ptr::null(), std::ptr::null(), 1);
            }
        }
        #[cfg(target_os = "macos")]
        let _ = std::process::Command::new("open").arg(&target).spawn();
        #[cfg(all(unix, not(target_os = "macos")))]
        let _ = std::process::Command::new("xdg-open").arg(&target).spawn();
    });
}

/// `w-<id>` for `?window=<id>` and `p-<panel>` for `?popout=<panel>`, so geometry restore finds the same
/// window next launch; else a counter.
fn label_for(url: &Url) -> String {
    let clean = |s: &str| {
        s.chars()
            .map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' { c } else { '-' })
            .collect::<String>()
    };
    if let Some((_, id)) = url.query_pairs().find(|(k, _)| k == "window") {
        return format!("w-{}", clean(&id));
    }
    if let Some((_, p)) = url.query_pairs().find(|(k, _)| k == "popout") {
        return format!("p-{}", clean(&p));
    }
    format!("popup-{}", POPUPS.fetch_add(1, Ordering::SeqCst))
}

/// Everything every window gets: the navigation guard, pop-out handling, drop mode, the 3D mouse shim.
/// `flow` is this window's sign-in state (shared by its navigation and new-window handlers).
///
/// An allowed navigation is never touched: returning `true` lets WebView2 carry on with the request as
/// the page made it (a form POST stays a POST). Only a refused one is handed to the system browser,
/// which can only ever GET it.
fn base<'a>(
    app: &'a AppHandle,
    label: &str,
    url: WebviewUrl,
    title: &str,
    o: &Opts,
    flow: Arc<origin::Flow>,
) -> WebviewWindowBuilder<'a, tauri::Wry, AppHandle> {
    let (app2, o2, flow2) = (app.clone(), o.clone(), flow.clone());
    let l = label.to_string();
    let mut b = WebviewWindowBuilder::new(app, label, url)
        .title(title)
        .inner_size(1400.0, 900.0)
        .on_navigation(move |u| {
            let Some(p) = POLICY.get() else { return false };
            let was = flow.signing_in();
            match flow.navigate(p, u) {
                origin::Nav::App => {
                    if was {
                        log_nav(&l, "sign-in returned to", u);
                    }
                    true
                }
                origin::Nav::Blank => true,
                origin::Nav::SignIn => {
                    log_nav(&l, "sign-in page", u);
                    true
                }
                origin::Nav::Followed => {
                    log_nav(&l, "sign-in passes through", u);
                    true
                }
                origin::Nav::Deny => {
                    log_nav(&l, "not shown here, opening in the system browser:", u);
                    open_external(u);
                    false
                }
            }
        })
        .on_new_window(move |u, f| open_popup(&app2, u, f, &o2, flow2.signing_in()));
    if o.spacemouse {
        b = b.initialization_script(include_str!("spacemouse_shim.js"));
    }
    if !o.native_drop {
        // Let the page see dropped files as File objects (same as in a browser).
        b = b.disable_drag_drop_handler();
    }
    b
}

fn open_popup(
    app: &AppHandle,
    url: Url,
    features: NewWindowFeatures,
    o: &Opts,
    opener_signing_in: bool,
) -> NewWindowResponse<tauri::Wry> {
    let Some(p) = POLICY.get() else { return NewWindowResponse::Deny };
    let kind = p.classify(&url, opener_signing_in);
    if !kind.allowed() {
        // A link with target=_blank, or window.open on another site: the system browser shows it.
        log_nav("pop-up", "not shown here, opening in the system browser:", &url);
        open_external(&url);
        return NewWindowResponse::Deny;
    }
    let sign_in = matches!(kind, origin::Nav::SignIn | origin::Nav::Followed);
    let label = if sign_in {
        // Sign-in pop-ups are transient: no saved geometry, never "the same pop-out again".
        log_nav("pop-up", "sign-in window", &url);
        format!("signin-{}", POPUPS.fetch_add(1, Ordering::SeqCst))
    } else {
        label_for(&url)
    };
    if let Some(existing) = app.get_webview_window(&label) {
        // Same pop-out asked for again: focus it, as window.open(url, sameName) does in a browser.
        let _ = existing.set_focus();
        return NewWindowResponse::Deny;
    }
    // The opener's request navigates the new webview to `url` (WebView2 does that itself, so window.opener
    // and a POST body are kept); it starts blank. It shares the opener's webview environment.
    let flow = Arc::new(origin::Flow::new(sign_in));
    let b = base(app, &label, WebviewUrl::External("about:blank".parse().unwrap()), "Ada Studio", o, flow)
        .window_features(features)
        .on_document_title_changed(|w, t| {
            let _ = w.set_title(&t);
        });
    match b.build() {
        Ok(window) => {
            // window.open(.., "popup,width=..") sizes the window itself, which wins over the saved geometry,
            // and WebView2 applies that size only after creation. Put the saved size and position back once it
            // has: from another thread, because doing it inside this handler deadlocks WebView2's callback.
            if !sign_in {
                let w2 = window.clone();
                std::thread::spawn(move || {
                    std::thread::sleep(std::time::Duration::from_millis(400));
                    let _ = w2.restore_state(StateFlags::SIZE | StateFlags::POSITION);
                });
            }
            NewWindowResponse::Create { window }
        }
        Err(e) => {
            eprintln!("[ada-desktop] pop-out failed: {e}");
            NewWindowResponse::Deny
        }
    }
}

/// The WebView2 environment with single sign-on through the Windows (OS primary) account enabled:
/// `ICoreWebView2EnvironmentOptions::AllowSingleSignOnUsingOSPrimaryAccount`. With it, the Microsoft
/// identity platform signs in with the work or school account Windows is signed in with (and sees the
/// device's identity, which device-based Conditional Access requires), as Edge does.
///
/// wry creates the environment itself and has no switch for this, but takes one made outside
/// (`with_environment`); pop-outs then share it through `window_features`. Everything else is what wry
/// would have set (its default browser arguments, the UI language), in the same user data folder
/// tauri would have used, so cookies and storage are those of earlier launches.
///
/// Called on the main thread before the first window (it pumps messages while WebView2 answers).
#[cfg(windows)]
fn sso_environment(
    data_dir: &std::path::Path,
) -> Result<webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2Environment, String> {
    use std::sync::mpsc;
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        CreateCoreWebView2EnvironmentWithOptions, ICoreWebView2EnvironmentOptions,
    };
    use webview2_com::{CoreWebView2EnvironmentOptions, CreateCoreWebView2EnvironmentCompletedHandler};
    use windows_core::{HSTRING, PCWSTR};

    extern "system" {
        fn GetUserDefaultUILanguage() -> u16;
        fn LCIDToLocaleName(lcid: u32, name: *mut u16, len: i32, flags: u32) -> i32;
    }
    #[link(name = "ole32")]
    extern "system" {
        fn OleInitialize(reserved: *mut std::ffi::c_void) -> i32;
    }
    // WebView2 needs an STA on this thread; the window code initialises it only when it creates the first
    // window, which is after this. A second initialisation (tao's) is harmless (S_FALSE).
    unsafe {
        OleInitialize(std::ptr::null_mut());
    }
    std::fs::create_dir_all(data_dir).map_err(|e| format!("{}: {e}", data_dir.display()))?;
    let lang = unsafe {
        let mut buf = [0u16; 85]; // LOCALE_NAME_MAX_LENGTH
        // LOCALE_ALLOW_NEUTRAL_NAMES, as wry does
        let n = LCIDToLocaleName(GetUserDefaultUILanguage() as u32, buf.as_mut_ptr(), buf.len() as i32, 0x0800_0000);
        String::from_utf16_lossy(&buf[..(n.max(1) as usize - 1)])
    };
    let options = CoreWebView2EnvironmentOptions::default();
    unsafe {
        // wry's defaults: no "mini menu", no SmartScreen.
        options.set_additional_browser_arguments(
            "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection".to_string(),
        );
        options.set_language(lang);
        options.set_allow_single_sign_on_using_os_primary_account(true);
    }
    let options: ICoreWebView2EnvironmentOptions = options.into();
    let (tx, rx) = mpsc::channel();
    unsafe {
        CreateCoreWebView2EnvironmentWithOptions(
            PCWSTR::null(),
            &HSTRING::from(data_dir.as_os_str()),
            &options,
            &CreateCoreWebView2EnvironmentCompletedHandler::create(Box::new(move |code, env| {
                let _ = tx.send(code.and_then(|_| env.ok_or_else(|| windows_core::Error::from_hresult(windows_core::HRESULT(0x80004003u32 as i32)))));
                Ok(())
            })),
        )
        .map_err(|e| e.to_string())?;
    }
    webview2_com::wait_with_pump(rx).map_err(|e| e.to_string())?.map_err(|e| e.to_string())
}

/// The message for a window WebView2 would not create. The usual cause: another ada-desktop with other
/// sign-in settings (an older version without single sign-on, or `--no-sso`, or the other way round)
/// runs on the same profile, and WebView2 runs one browser per profile with one set of options
/// (ERROR_INVALID_STATE, 0x8007139F).
fn profile_in_use_hint(e: &str) -> String {
    if e.contains("8007139F") {
        format!(
            "could not open the window: another ada-desktop window with different sign-in settings is open \
             (an older version, or one started with/without --no-sso), and they cannot share the browser \
             profile.\n\nClose all ada-desktop windows and start again.\n\n({e})"
        )
    } else {
        format!("could not open the window: {e}")
    }
}

fn spacemouse_log() {
    if let Ok(api) = hidapi::HidApi::new() {
        let l = spacemouse::list(&api);
        println!("3D mouse HID interfaces: {}", l.len());
        for s in l {
            println!("  {s}");
        }
    }
    println!("reading (Ctrl+C to stop)...");
    spacemouse::run(|ev| println!("{ev:?}"));
}

fn main() {
    #[cfg(windows)]
    attach_parent_console();

    let mut url: Option<String> = None;
    let mut trust: Option<String> = None;
    let mut auth: Vec<String> = Vec::new();
    let mut insecure = false;
    let mut title = "Ada Studio".to_string();
    let mut o = Opts { native_drop: false, spacemouse: true, sso: true };
    let mut sm_log = false;
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--url" => url = args.next(),
            "--trust-origin" => trust = args.next(),
            "--auth-origin" => auth.extend(args.next()),
            "--insecure" => insecure = true,
            "--title" => title = args.next().unwrap_or(title),
            "--native-drop" => o.native_drop = true,
            "--no-spacemouse" => o.spacemouse = false,
            "--no-sso" => o.sso = false,
            "--spacemouse-log" => sm_log = true,
            "--version" => {
                let wv = tauri::webview_version().unwrap_or_else(|_| "not installed".into());
                println!("ada-desktop {} (webview {wv})", env!("CARGO_PKG_VERSION"));
                return;
            }
            "-h" | "--help" => {
                println!("{USAGE}");
                return;
            }
            other => {
                eprintln!("ada-desktop: unknown argument {other}\n\n{USAGE}");
                std::process::exit(2);
            }
        }
    }
    if sm_log {
        spacemouse_log();
        return;
    }
    let Some(url) = url.and_then(|u| u.parse::<Url>().ok()) else {
        eprintln!("ada-desktop: --url is required\n\n{USAGE}");
        std::process::exit(2);
    };
    let policy = match origin::Policy::new(&url, trust.as_deref(), &auth, insecure) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("ada-desktop: {e}");
            std::process::exit(2);
        }
    };
    let ipc_pattern = policy.ipc_pattern();
    eprintln!(
        "[ada-desktop] trusted origin {} (native events granted); sign-in origins: {}{}",
        policy.trusted().origin().ascii_serialization(),
        if policy.auth_origins().is_empty() {
            "none".to_string()
        } else {
            policy.auth_origins().iter().map(|a| a.origin().ascii_serialization()).collect::<Vec<_>>().join(", ")
        },
        if policy.entra() { " (+ the built-in Microsoft Entra ID sign-in hosts)" } else { "" }
    );
    let _ = POLICY.set(policy);

    if let Err(e) = tauri::webview_version() {
        fatal(&format!(
            "the Microsoft Edge WebView2 Runtime is not installed ({e}).\n\n\
             Install the Evergreen runtime from https://developer.microsoft.com/microsoft-edge/webview2/ \
             and start ada desktop again."
        ));
    }

    let o_setup = o.clone();
    let app = tauri::Builder::default()
        .plugin(
            tauri_plugin_window_state::Builder::default()
                .with_state_flags(StateFlags::all() - StateFlags::VISIBLE)
                .build(),
        )
        .on_webview_event(|wv, ev| {
            if let tauri::WebviewEvent::DragDrop(d) = ev {
                // Only with --native-drop; nothing in the viewer consumes native paths yet.
                eprintln!("[ada-desktop] drag-drop on {}: {:?}", wv.label(), d);
            }
        })
        .invoke_handler(tauri::generate_handler![spacemouse_status])
        .setup(move |app| {
            let o = o_setup;
            // IPC for the viewer's origin only, and only the 3D mouse events + spacemouse_status. Built at
            // startup because the origin is not known when the app is compiled.
            app.add_capability(
                CapabilityBuilder::new("viewer")
                    .local(false)
                    .remote(ipc_pattern.clone())
                    .window("*")
                    .permission("core:event:allow-listen")
                    .permission("core:event:allow-unlisten")
                    .permission("allow-spacemouse-status"),
            )?;
            if o.spacemouse {
                let h = app.handle().clone();
                std::thread::spawn(move || {
                    spacemouse::run(|ev| match ev {
                        spacemouse::Ev::Status(s) => {
                            eprintln!("[ada-desktop] 3D mouse: {s}");
                            SM_OPEN.store(s.starts_with("opened"), Ordering::Relaxed);
                            let _ = h.emit("spacemouse://status", s);
                        }
                        spacemouse::Ev::Motion(a) => {
                            let _ = h.emit(
                                "spacemouse://motion",
                                serde_json::json!({"tx":a[0],"ty":a[1],"tz":a[2],"rx":a[3],"ry":a[4],"rz":a[5]}),
                            );
                        }
                        spacemouse::Ev::Buttons(m) => {
                            let _ = h.emit("spacemouse://buttons", serde_json::json!({ "mask": m }));
                        }
                    })
                });
            }
            // The main window keeps --title; pop-outs follow their page's title.
            let main = || {
                base(
                    app.handle(),
                    "main",
                    WebviewUrl::External(url.clone()),
                    &title,
                    &o,
                    Arc::new(origin::Flow::default()),
                )
            };
            #[cfg(windows)]
            if o.sso {
                // The folder tauri gives WebView2 by default, so the profile (cookies, storage) is the same
                // with and without SSO. WEBVIEW2_USER_DATA_FOLDER still overrides it, as it does for wry.
                let built = app
                    .path()
                    .app_local_data_dir()
                    .map_err(|e| e.to_string())
                    .and_then(|d| sso_environment(&d))
                    .and_then(|env| main().with_environment(env).build().map_err(|e| e.to_string()));
                // No fallback to a window without SSO on failure: WebView2 then refuses that one too, because
                // this process already made an environment with other options for the same folder.
                match built {
                    Ok(_) => {
                        eprintln!("[ada-desktop] single sign-on with the Windows account: on (--no-sso turns it off)");
                        return Ok(());
                    }
                    Err(e) => fatal(&profile_in_use_hint(&e)),
                }
            }
            if let Err(e) = main().build() {
                fatal(&profile_in_use_hint(&e.to_string()));
            }
            Ok(())
        })
        .build(tauri::generate_context!());
    let app = match app {
        Ok(a) => a,
        Err(e) => fatal(&format!("could not open the window: {e}")),
    };
    app.run(|app, ev| {
        // Persist geometry of every open window when the app is about to exit.
        if let tauri::RunEvent::ExitRequested { .. } = ev {
            let _ = app.save_window_state(StateFlags::all());
        }
    });
}
