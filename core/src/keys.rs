//! API keys, one per provider, in keys.json in the data folder (mode 0600), as
//! codex-auth.json keeps the ChatGPT tokens. Nothing else stores or prints a key.

use std::collections::BTreeMap;
use std::io::Write;
use std::path::Path;

use anyhow::{Result, bail};

fn read(data_dir: &Path) -> BTreeMap<String, String> {
    std::fs::read_to_string(data_dir.join("keys.json"))
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

pub fn get(data_dir: &Path, provider: &str) -> Option<String> {
    read(data_dir).remove(provider).filter(|k| !k.is_empty())
}

/// An empty key removes the provider's key.
pub fn set(data_dir: &Path, provider: &str, key: &str) -> Result<()> {
    let key = key.trim();
    if key.chars().any(char::is_whitespace) {
        bail!("a key has no spaces or line breaks");
    }
    let mut keys = read(data_dir);
    if key.is_empty() {
        keys.remove(provider);
    } else {
        keys.insert(provider.into(), key.into());
    }
    std::fs::create_dir_all(data_dir)?;
    // Written to a private file first, so the key is never readable by others, even briefly.
    let tmp = data_dir.join("keys.json.tmp");
    let _ = std::fs::remove_file(&tmp);
    let mut f = std::fs::OpenOptions::new();
    f.write(true).create_new(true);
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::mode(&mut f, 0o600);
    f.open(&tmp)?.write_all(serde_json::to_string_pretty(&keys)?.as_bytes())?;
    std::fs::rename(&tmp, data_dir.join("keys.json"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys_are_private_and_removable() -> Result<()> {
        let dir = std::env::temp_dir().join(format!("arity-keys-{}", std::process::id()));
        set(&dir, "nous", " sk-1 \n")?;
        assert_eq!(get(&dir, "nous").as_deref(), Some("sk-1"));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(std::fs::metadata(dir.join("keys.json"))?.permissions().mode() & 0o777, 0o600);
        }
        assert!(set(&dir, "nous", "sk 2").is_err());
        set(&dir, "nous", "")?;
        assert_eq!(get(&dir, "nous"), None);
        std::fs::remove_dir_all(&dir)?;
        Ok(())
    }
}
