//! Which origins a window may show, and which one may talk to the shell.
//!
//! Three kinds of origin exist:
//! * the **trusted** origin: the viewer (`--url`). Windows may show it, pop-outs may open on it and it
//!   (alone) is granted IPC;
//! * **sign-in** origins: the identity provider's pages (`--auth-origin`, plus a built-in set of
//!   Microsoft Entra ID hosts when the provider is Entra, see [`ENTRA_HOSTS`]). A window may be
//!   navigated there and they may open sign-in pop-ups, but they never get IPC;
//! * everything else: not shown, handed to the system browser, except **during a sign-in**: once a
//!   window is on a sign-in page, the provider may send it on to any `https` page (a federated
//!   identity provider, an MFA service) until it comes back to the trusted origin. See [`Flow`].
//!
//! No tauri runtime here, so it is unit tested without a window.

use std::sync::atomic::{AtomicBool, Ordering};
use tauri::Url;

/// Hosts the Microsoft identity platform (Entra ID, work/school and personal Microsoft accounts) signs
/// users in on, besides the authorize endpoint the discovery document names: the sign-in host under
/// its other names, personal accounts, device and certificate authentication, seamless SSO, and the
/// "more information required" (MFA registration) interrupt. `https` only; added when one of the
/// sign-in origins is a Microsoft login host.
pub const ENTRA_HOSTS: &[&str] = &[
    "login.microsoftonline.com",
    "login.microsoft.com",
    "login.windows.net",
    "sts.windows.net",
    "login.live.com",
    "account.live.com",
    "device.login.microsoftonline.com",
    "certauth.login.microsoftonline.com",
    "autologon.microsoftazuread-sso.com",
    "mysignins.microsoft.com",
];
/// Sub-domains of these are Entra sign-in hosts too (tenant-specific certificate authentication).
pub const ENTRA_SUFFIXES: &[&str] = &[".certauth.login.microsoftonline.com"];

/// Whether `host` is one of Microsoft's login hosts (global cloud and the national clouds), i.e. the
/// provider is Microsoft Entra ID.
pub fn is_entra_login_host(host: &str) -> bool {
    matches!(
        host,
        "login.microsoftonline.com"
            | "login.microsoft.com"
            | "login.windows.net"
            | "sts.windows.net"
            | "login.microsoftonline.us"
            | "login.chinacloudapi.cn"
            | "login.partner.microsoftonline.cn"
    )
}

/// `127.0.0.1`, `localhost` or `[::1]`, over http: the local viewer `ada desktop` starts.
pub fn is_loopback(url: &Url) -> bool {
    url.scheme() == "http" && matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "[::1]"))
}

/// Parse `s` as a bare origin (scheme, host, port; a path, query or fragment is dropped).
/// `https` is required, except for loopback http and for `insecure` (lab setups).
pub fn parse_origin(s: &str, insecure: bool) -> Result<Url, String> {
    let u: Url = s.trim().parse().map_err(|e| format!("{s}: not a URL ({e})"))?;
    if !matches!(u.scheme(), "http" | "https") || u.host_str().is_none() {
        return Err(format!("{s}: must be an http(s) URL with a host"));
    }
    if !u.username().is_empty() || u.password().is_some() {
        return Err(format!("{s}: must not contain credentials"));
    }
    if u.scheme() != "https" && !is_loopback(&u) && !insecure {
        return Err(format!("{s}: plain http is only accepted for loopback; use https (or --insecure)"));
    }
    let mut o = u;
    o.set_path("/");
    o.set_query(None);
    o.set_fragment(None);
    Ok(o)
}

/// What a navigation is, for one window.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Nav {
    /// The trusted origin (or a `blob:` of it): the app. Ends a sign-in.
    App,
    /// `about:blank` and the like: allowed, changes nothing.
    Blank,
    /// A sign-in origin: allowed (no IPC). Starts or continues a sign-in.
    SignIn,
    /// Another `https` page, reached while the window is signing in: allowed (no IPC).
    Followed,
    /// Not shown in the window: handed to the system browser (http/https/mailto) or dropped.
    Deny,
}

impl Nav {
    pub fn allowed(self) -> bool {
        self != Nav::Deny
    }
}

/// Whether one window is in the middle of a sign-in: it went from the app to a sign-in page and has
/// not come back yet. Every window has its own.
#[derive(Debug, Default)]
pub struct Flow(AtomicBool);

impl Flow {
    pub fn new(signing_in: bool) -> Flow {
        Flow(AtomicBool::new(signing_in))
    }

    pub fn signing_in(&self) -> bool {
        self.0.load(Ordering::Relaxed)
    }

    /// Classify a main-frame navigation of this window and track the sign-in state.
    pub fn navigate(&self, policy: &Policy, url: &Url) -> Nav {
        let n = policy.classify(url, self.signing_in());
        match n {
            Nav::App => self.0.store(false, Ordering::Relaxed),
            Nav::SignIn => self.0.store(true, Ordering::Relaxed),
            Nav::Blank | Nav::Followed | Nav::Deny => {}
        }
        n
    }
}

/// `https://host/path` without query or fragment, for logs: sign-in URLs carry codes, state and hints.
pub fn redact(url: &Url) -> String {
    match url.host_str() {
        Some(h) => format!("{}://{}{}", url.scheme(), h, url.path()),
        None => url.scheme().to_string() + ":",
    }
}

#[derive(Debug, Clone)]
pub struct Policy {
    trusted: Url,
    auth: Vec<Url>,
    /// Microsoft Entra ID: the built-in hosts ([`ENTRA_HOSTS`]) are sign-in origins too.
    entra: bool,
}

impl Policy {
    /// `url` is the page to open. Without `trust` it must be loopback http (the local viewer). With
    /// `trust` (a remote server) its origin must be exactly `url`'s.
    pub fn new(url: &Url, trust: Option<&str>, auth: &[String], insecure: bool) -> Result<Policy, String> {
        let trusted = match trust {
            None => {
                if !is_loopback(url) {
                    return Err(format!(
                        "--url must be http on 127.0.0.1, localhost or [::1], not {url} \
                         (a remote server needs --trust-origin ORIGIN)"
                    ));
                }
                parse_origin(url.as_str(), false)?
            }
            Some(t) => {
                let t = parse_origin(t, insecure)?;
                if url.origin() != t.origin() {
                    return Err(format!("--url {url} is not on the trusted origin {}", t.as_str()));
                }
                t
            }
        };
        let mut idp = Vec::new();
        for a in auth {
            let o = parse_origin(a, insecure).map_err(|e| format!("--auth-origin {e}"))?;
            if o.origin() != trusted.origin() && !idp.iter().any(|x: &Url| x.origin() == o.origin()) {
                idp.push(o);
            }
        }
        let entra = idp.iter().any(|a| a.scheme() == "https" && a.host_str().is_some_and(is_entra_login_host));
        Ok(Policy { trusted, auth: idp, entra })
    }

    pub fn trusted(&self) -> &Url {
        &self.trusted
    }

    pub fn auth_origins(&self) -> &[Url] {
        &self.auth
    }

    /// Whether the built-in Microsoft Entra ID hosts are sign-in origins.
    pub fn entra(&self) -> bool {
        self.entra
    }

    fn is_trusted(&self, url: &Url) -> bool {
        url.origin() == self.trusted.origin()
    }

    /// A sign-in origin: an `--auth-origin`, or (Entra) a built-in Microsoft sign-in host over https on
    /// the default port.
    pub fn is_sign_in(&self, url: &Url) -> bool {
        if self.auth.iter().any(|a| a.origin() == url.origin()) {
            return true;
        }
        self.entra
            && url.scheme() == "https"
            && url.port().is_none()
            && url
                .host_str()
                .is_some_and(|h| ENTRA_HOSTS.contains(&h) || ENTRA_SUFFIXES.iter().any(|s| h.ends_with(s)))
    }

    /// What a main-frame navigation of a window is; `signing_in` is that window's [`Flow`] state.
    pub fn classify(&self, url: &Url, signing_in: bool) -> Nav {
        if url.scheme() == "about" {
            Nav::Blank
        } else if self.is_trusted(url) {
            Nav::App
        } else if self.is_sign_in(url) {
            Nav::SignIn
        } else if signing_in && url.scheme() == "https" && url.host_str().is_some() {
            Nav::Followed
        } else {
            Nav::Deny
        }
    }

    /// What a window may navigate to outside a sign-in: the viewer (its `blob:` URLs included), a blank
    /// page, and the sign-in pages of the identity provider.
    #[cfg(test)]
    pub fn allows_navigation(&self, url: &Url) -> bool {
        self.classify(url, false).allowed()
    }

    /// What a new native window may open on: the viewer, and, for a sign-in, the provider's pages
    /// (a pop-up the provider opens keeps `window.opener` and its POST body, because WebView2 itself
    /// navigates the new window). `signing_in` is the opener's [`Flow`] state: a window in the middle
    /// of a sign-in may open any `https` pop-up, which then counts as signing in too.
    #[cfg(test)]
    pub fn allows_popup(&self, url: &Url, signing_in: bool) -> bool {
        self.classify(url, signing_in).allowed()
    }

    /// The remote-URL pattern for the IPC capability: the trusted origin and nothing else.
    pub fn ipc_pattern(&self) -> String {
        format!("{}/*", self.trusted.origin().ascii_serialization())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn u(s: &str) -> Url {
        s.parse().unwrap()
    }

    #[test]
    fn local_viewer_must_be_loopback_http() {
        assert!(Policy::new(&u("http://127.0.0.1:38500/"), None, &[], false).is_ok());
        assert!(Policy::new(&u("http://localhost:5173/x?y=1"), None, &[], false).is_ok());
        assert!(Policy::new(&u("https://studio.example.com/"), None, &[], false).is_err());
        assert!(Policy::new(&u("http://10.0.0.5:8000/"), None, &[], true).is_err());
    }

    #[test]
    fn remote_needs_https_unless_insecure() {
        let url = u("https://studio.example.com/app/");
        assert!(Policy::new(&url, Some("https://studio.example.com"), &[], false).is_ok());
        let plain = u("http://lab.example.com:8080/");
        assert!(Policy::new(&plain, Some("http://lab.example.com:8080"), &[], false).is_err());
        assert!(Policy::new(&plain, Some("http://lab.example.com:8080"), &[], true).is_ok());
    }

    #[test]
    fn trusted_origin_must_match_the_url() {
        let url = u("https://studio.example.com/");
        assert!(Policy::new(&url, Some("https://other.example.com"), &[], false).is_err());
        assert!(Policy::new(&url, Some("https://studio.example.com:8443"), &[], false).is_err());
        assert!(Policy::new(&url, Some("https://studio.example.com:443/path?q"), &[], false).is_ok());
    }

    #[test]
    fn credentials_and_odd_schemes_are_refused() {
        assert!(parse_origin("https://user:pw@studio.example.com", false).is_err());
        assert!(parse_origin("file:///c:/x", false).is_err());
        assert!(parse_origin("javascript:alert(1)", false).is_err());
        assert!(parse_origin("not a url", false).is_err());
    }

    #[test]
    fn navigation_allows_viewer_idp_and_blank_only() {
        let p = Policy::new(
            &u("https://studio.example.com/"),
            Some("https://studio.example.com"),
            &["https://login.example.org/tenant/v2".into()],
            false,
        )
        .unwrap();
        assert!(p.allows_navigation(&u("https://studio.example.com/auth/callback?code=1")));
        assert!(p.allows_navigation(&u("blob:https://studio.example.com/5f1c")));
        assert!(p.allows_navigation(&u("about:blank")));
        assert!(p.allows_navigation(&u("https://login.example.org/tenant/oauth2/authorize")));
        assert!(!p.allows_navigation(&u("https://evil.example.net/")));
        assert!(!p.allows_navigation(&u("https://studio.example.com.evil.net/")));
        assert!(!p.allows_navigation(&u("http://studio.example.com/"))); // scheme is part of the origin
        assert!(!p.allows_navigation(&u("https://studio.example.com:8443/")));
        assert!(!p.allows_navigation(&u("file:///c:/windows/win.ini")));
        assert!(!p.allows_navigation(&u("blob:https://evil.example.net/5f1c")));
    }

    #[test]
    fn popups_open_on_the_viewer_and_sign_in_pages_only() {
        let p = Policy::new(
            &u("https://studio.example.com/"),
            Some("https://studio.example.com"),
            &["https://login.example.org".into()],
            false,
        )
        .unwrap();
        assert!(p.allows_popup(&u("https://studio.example.com/?popout=scene"), false));
        assert!(p.allows_popup(&u("https://login.example.org/authorize"), false));
        assert!(!p.allows_popup(&u("https://docs.example.net/"), false));
        // A sign-in page may open a pop-up anywhere https (a security key, a federated provider) ...
        assert!(p.allows_popup(&u("https://mfa.example.net/start"), true));
        // ... but not plain http or other schemes.
        assert!(!p.allows_popup(&u("http://mfa.example.net/start"), true));
        assert!(!p.allows_popup(&u("ms-settings:privacy"), true));
    }

    fn entra() -> Policy {
        Policy::new(
            &u("https://studio.example.com/"),
            Some("https://studio.example.com"),
            &["https://login.microsoftonline.com".into()],
            false,
        )
        .unwrap()
    }

    #[test]
    fn entra_issuer_adds_the_built_in_sign_in_hosts() {
        let p = entra();
        assert!(p.entra());
        for h in ENTRA_HOSTS {
            assert!(p.allows_navigation(&u(&format!("https://{h}/common/oauth2/v2.0/authorize"))), "{h}");
            assert!(!p.allows_navigation(&u(&format!("http://{h}/"))), "{h} over http");
            assert!(!p.allows_navigation(&u(&format!("https://{h}:8443/"))), "{h} on another port");
        }
        assert!(p.allows_navigation(&u("https://t1234.certauth.login.microsoftonline.com/x")));
        assert!(!p.allows_navigation(&u("https://login.microsoftonline.com.evil.example.net/")));
        assert!(!p.allows_navigation(&u("https://evilcertauth.login.microsoftonline.com.example.net/")));
        assert!(!p.allows_navigation(&u("https://myaccount.microsoft.com/")));
        // Not for another provider.
        let other = Policy::new(
            &u("https://studio.example.com/"),
            Some("https://studio.example.com"),
            &["https://login.example.org".into()],
            false,
        )
        .unwrap();
        assert!(!other.entra());
        assert!(!other.allows_navigation(&u("https://login.live.com/")));
        // National clouds count as Entra too.
        assert!(is_entra_login_host("login.microsoftonline.us"));
        assert!(!is_entra_login_host("login.example.org"));
    }

    #[test]
    fn a_sign_in_may_pass_through_other_https_hosts_until_it_returns() {
        let p = entra();
        let f = Flow::default();
        // Not signing in: a foreign page is refused.
        assert_eq!(f.navigate(&p, &u("https://sts.example.net/adfs/ls/")), Nav::Deny);
        assert_eq!(f.navigate(&p, &u("https://studio.example.com/")), Nav::App);
        assert_eq!(f.navigate(&p, &u("https://login.microsoftonline.com/t/oauth2/v2.0/authorize?x")), Nav::SignIn);
        assert!(f.signing_in());
        // The provider federates to an on-premise service and back (form POSTs included: allowed
        // navigations proceed natively, nothing is re-issued).
        assert_eq!(f.navigate(&p, &u("https://sts.example.net/adfs/ls/?wa=wsignin1.0")), Nav::Followed);
        assert_eq!(f.navigate(&p, &u("about:blank")), Nav::Blank);
        assert!(f.signing_in());
        assert_eq!(f.navigate(&p, &u("http://sts.example.net/")), Nav::Deny);
        assert_eq!(f.navigate(&p, &u("file:///c:/x")), Nav::Deny);
        assert_eq!(f.navigate(&p, &u("https://login.microsoftonline.com/login.srf")), Nav::SignIn);
        assert_eq!(f.navigate(&p, &u("https://studio.example.com/auth/callback?code=1")), Nav::App);
        // Back in the app: the next foreign page goes to the system browser again.
        assert!(!f.signing_in());
        assert_eq!(f.navigate(&p, &u("https://sts.example.net/")), Nav::Deny);
    }

    #[test]
    fn logs_drop_query_and_fragment() {
        assert_eq!(
            redact(&u("https://login.example.org/t/oauth2/authorize?code=secret#state")),
            "https://login.example.org/t/oauth2/authorize"
        );
        assert_eq!(redact(&u("about:blank")), "about:");
    }

    #[test]
    fn ipc_is_granted_to_the_trusted_origin_alone() {
        let p = Policy::new(
            &u("https://studio.example.com:443/"),
            Some("https://studio.example.com"),
            &["https://login.example.org".into(), "https://studio.example.com".into()],
            false,
        )
        .unwrap();
        assert_eq!(p.ipc_pattern(), "https://studio.example.com/*");
        // The trusted origin repeated as an auth origin is not a second entry; the IdP is no IPC origin.
        assert_eq!(p.auth_origins().len(), 1);
        let l = Policy::new(&u("http://127.0.0.1:38500/?file=a"), None, &[], false).unwrap();
        assert_eq!(l.ipc_pattern(), "http://127.0.0.1:38500/*");
    }

    #[test]
    fn auth_origins_follow_the_same_scheme_rule() {
        let url = u("https://studio.example.com/");
        let bad = ["http://idp.example.org".to_string()];
        assert!(Policy::new(&url, Some("https://studio.example.com"), &bad, false).is_err());
        assert!(Policy::new(&url, Some("https://studio.example.com"), &bad, true).is_ok());
    }
}
