//! Native 3D mouse (SpaceMouse) reader: hidapi, no WebHID chooser, no vendor SDK or driver.
//!
//! A reader thread keeps (re)trying to open the device (every few seconds while none is connected) and
//! parses the input reports the same way the frontend's `hidReport.ts` does:
//!   report 1: tx,ty,tz (i16 LE) -- 12 bytes of payload on newer devices: tx,ty,tz,rx,ry,rz
//!   report 2: rx,ry,rz (i16 LE)
//!   report 3: button bitmask
//! Motion is delivered coalesced to at most ~60 Hz as the six axes in device units (about +-350 full scale).
use hidapi::{DeviceInfo, HidApi};
use std::time::{Duration, Instant};

/// USB vendor id of current 3Dconnexion devices.
const VENDOR_CURRENT: u16 = 0x256f;
/// Older devices (SpaceNavigator, SpacePilot, SpaceExplorer, ...) were sold under Logitech's vendor id;
/// only these product ids of it are 3D mice.
const VENDOR_LEGACY: u16 = 0x046d;
const LEGACY_PRODUCTS: [u16; 11] = [0xc603, 0xc605, 0xc606, 0xc621, 0xc623, 0xc625, 0xc626, 0xc627, 0xc628, 0xc629, 0xc62b];
const MIN_EMIT: Duration = Duration::from_millis(16);
const RESCAN: Duration = Duration::from_secs(3);

#[derive(Debug, Clone)]
pub enum Ev {
    Status(String),
    Motion([i16; 6]),
    Buttons(u32),
}

fn is_3d_mouse(d: &DeviceInfo) -> bool {
    d.vendor_id() == VENDOR_CURRENT || (d.vendor_id() == VENDOR_LEGACY && LEGACY_PRODUCTS.contains(&d.product_id()))
}

/// Every HID interface of a 3D mouse: `vid:pid usage_page/usage product path`.
pub fn list(api: &HidApi) -> Vec<String> {
    api.device_list()
        .filter(|d| is_3d_mouse(d))
        .map(|d| {
            format!(
                "{:04x}:{:04x} usage {:#04x}/{:#04x} {:?} {}",
                d.vendor_id(),
                d.product_id(),
                d.usage_page(),
                d.usage(),
                d.product_string().unwrap_or(""),
                d.path().to_string_lossy()
            )
        })
        .collect()
}

fn le(b: &[u8], i: usize) -> i16 {
    i16::from_le_bytes([b[i], b[i + 1]])
}

/// Blocks forever; run on its own thread. `sink` gets status text, throttled motion and button masks.
pub fn run(sink: impl Fn(Ev)) {
    let mut api = match HidApi::new() {
        Ok(a) => a,
        Err(e) => return sink(Ev::Status(format!("hidapi init failed: {e}"))),
    };
    let mut last_status = String::new();
    // A device the vendor driver holds fails to open every RESCAN; say so once, not every 3 s.
    let mut say = |s: String| {
        if s != last_status {
            last_status = s.clone();
            sink(Ev::Status(s));
        }
    };
    loop {
        let _ = api.refresh_devices();
        // Generic desktop page (0x01), multi-axis controller (0x08): the interface that carries motion.
        let found = api
            .device_list()
            .find(|d| is_3d_mouse(d) && d.usage_page() == 0x01 && d.usage() == 0x08)
            .map(|d| (d.path().to_owned(), d.product_string().unwrap_or("3D mouse").to_string()));
        let Some((path, name)) = found else {
            std::thread::sleep(RESCAN);
            continue;
        };
        let dev = match api.open_path(&path) {
            Ok(d) => d,
            Err(e) => {
                say(format!("found {name} but could not open it ({e}); another program may hold it"));
                std::thread::sleep(RESCAN);
                continue;
            }
        };
        say(format!("opened {name}"));
        let (mut axes, mut dirty, mut last) = ([0i16; 6], false, Instant::now() - MIN_EMIT);
        let mut buf = [0u8; 64];
        loop {
            match dev.read_timeout(&mut buf, 16) {
                Ok(0) => {}
                Ok(n) => {
                    let (id, p) = (buf[0], &buf[1..n]);
                    match id {
                        1 if p.len() >= 6 => {
                            axes[0] = le(p, 0);
                            axes[1] = le(p, 2);
                            axes[2] = le(p, 4);
                            if p.len() >= 12 {
                                axes[3] = le(p, 6);
                                axes[4] = le(p, 8);
                                axes[5] = le(p, 10);
                            }
                            dirty = true;
                        }
                        2 if p.len() >= 6 => {
                            axes[3] = le(p, 0);
                            axes[4] = le(p, 2);
                            axes[5] = le(p, 4);
                            dirty = true;
                        }
                        3 => {
                            let mut m = 0u32;
                            for (i, b) in p.iter().take(4).enumerate() {
                                m |= (*b as u32) << (8 * i);
                            }
                            sink(Ev::Buttons(m));
                        }
                        _ => {}
                    }
                }
                Err(e) => {
                    say(format!("{name} disconnected ({e})"));
                    sink(Ev::Motion([0; 6]));
                    break;
                }
            }
            if dirty && last.elapsed() >= MIN_EMIT {
                sink(Ev::Motion(axes));
                dirty = false;
                last = Instant::now();
            }
        }
        drop(dev);
        std::thread::sleep(RESCAN);
    }
}
