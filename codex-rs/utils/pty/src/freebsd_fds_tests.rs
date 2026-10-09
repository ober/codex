use std::io::Read;
use std::os::fd::AsRawFd;
use std::os::fd::FromRawFd;
use std::os::fd::OwnedFd;
use std::os::unix::process::CommandExt;
use std::process::Command;
use std::process::Stdio;

#[test]
fn cleanup_preserves_explicit_fds_without_procfs() -> anyhow::Result<()> {
    let Some(python) = crate::tests::find_python() else {
        eprintln!("descriptor test requires Python");
        return Ok(());
    };
    let file = tempfile::tempfile()?;
    let unpreserved = unsafe { libc::fcntl(file.as_raw_fd(), libc::F_DUPFD, 200) };
    anyhow::ensure!(unpreserved >= 0, "failed to reserve descriptor");
    // SAFETY: fcntl returned a newly owned descriptor.
    let unpreserved = unsafe { OwnedFd::from_raw_fd(unpreserved) };
    let preserved = unsafe { libc::fcntl(file.as_raw_fd(), libc::F_DUPFD, 210) };
    anyhow::ensure!(preserved >= 0, "failed to reserve descriptor");
    // SAFETY: fcntl returned a newly owned descriptor.
    let preserved = unsafe { OwnedFd::from_raw_fd(preserved) };
    let unpreserved_fd = unpreserved.as_raw_fd();
    let preserved_fd = preserved.as_raw_fd();

    let mut command = Command::new(python);
    command
        .arg("-c")
        .arg(
            "import errno, os, sys\ndef open_fd(fd):\n    try:\n        os.fstat(int(fd)); return True\n    except OSError as error:\n        if error.errno == errno.EBADF: return False\n        raise\nprint([open_fd(fd) for fd in sys.argv[1:]])",
        )
        .args([unpreserved_fd.to_string(), preserved_fd.to_string()])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    // SAFETY: this hook only invokes the allocation-free FreeBSD cleanup path.
    unsafe {
        command.pre_exec(move || {
            crate::pty::close_inherited_fds_except(&[preserved_fd]);
            Ok(())
        });
    }
    let mut child = command.spawn()?;
    let mut stdout = String::new();
    child
        .stdout
        .take()
        .expect("piped stdout")
        .read_to_string(&mut stdout)?;
    let output = child.wait_with_output()?;
    anyhow::ensure!(
        output.status.success(),
        "descriptor probe failed: status={:?}, stderr={:?}",
        output.status,
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(stdout.trim(), "[False, True]");
    Ok(())
}
