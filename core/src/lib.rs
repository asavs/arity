pub mod codec;
pub mod runner;
pub mod secrets;
pub mod store;
pub mod tools;
pub mod types;
pub mod workspace;

use std::path::PathBuf;

/// Resolve Arity's data directory (`$ARITY_HOME`, or `~/Library/Application Support/Arity` on macOS).
pub fn default_data_dir() -> PathBuf {
    if let Ok(p) = std::env::var("ARITY_HOME") {
        let trimmed = p.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }
    if let Ok(home) = std::env::var("HOME") {
        #[cfg(target_os = "macos")]
        {
            return PathBuf::from(home).join("Library/Application Support/Arity");
        }
        #[cfg(not(target_os = "macos"))]
        {
            return PathBuf::from(home).join(".local/share/arity");
        }
    }
    PathBuf::from(".arity")
}
