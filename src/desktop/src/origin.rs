//! Which origins a window may show, and which one may talk to the shell.
//!
//! Three kinds of origin exist:
//! * the **trusted** origin: the viewer (`--url`). Windows may show it, pop-outs may open on it and it
//!   (alone) is granted IPC;
//! * **auth** origins (`--auth-origin`): the identity provider's sign-in pages. A window may be
//!   *navigated* there (the sign-in redirect) but no pop-out opens on them and they get no IPC;
//! * everything else: not shown, handed to the system browser.
//!
//! No tauri runtime here, so it is unit tested without a window.

use tauri::Url;

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

#[derive(Debug, Clone)]
pub struct Policy {
    trusted: Url,
    auth: Vec<Url>,
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
        Ok(Policy { trusted, auth: idp })
    }

    pub fn trusted(&self) -> &Url {
        &self.trusted
    }

    pub fn auth_origins(&self) -> &[Url] {
        &self.auth
    }

    fn is_trusted(&self, url: &Url) -> bool {
        url.origin() == self.trusted.origin()
    }

    /// What a window may navigate to: the viewer (its `blob:` URLs included), a blank page, and the
    /// sign-in pages of the identity provider.
    pub fn allows_navigation(&self, url: &Url) -> bool {
        url.scheme() == "about" || self.is_trusted(url) || self.auth.iter().any(|a| a.origin() == url.origin())
    }

    /// What a new native window may open on: the viewer only. A sign-in page in a pop-out would get
    /// no way back to the opener; the system browser shows it instead.
    pub fn allows_popup(&self, url: &Url) -> bool {
        url.scheme() == "about" || self.is_trusted(url)
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
    fn popups_open_on_the_viewer_only() {
        let p = Policy::new(
            &u("https://studio.example.com/"),
            Some("https://studio.example.com"),
            &["https://login.example.org".into()],
            false,
        )
        .unwrap();
        assert!(p.allows_popup(&u("https://studio.example.com/?popout=scene")));
        assert!(!p.allows_popup(&u("https://login.example.org/authorize")));
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
